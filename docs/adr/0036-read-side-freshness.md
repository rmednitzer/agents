# ADR 0036: Read-side freshness gating and refusal-as-data (BL-246)

- Status: Accepted
- Date: 2026-06-13
- Authors: rmednitzer
- Builds on: ADR 0002 (behavioural contracts, predicates), ADR 0004
  (memory), ADR 0012 (write-side provenance), ADR 0030 (the SOFT
  predicate-factory shape, degraded disposition)

## Context

Run provenance (ADR 0012) is write-side: a `RunRecord` attests what a run
did. There is no read-side freshness contract that stamps a value with an
as-of instant and forces the agent to treat a stale value as suspect
before relying on it. And BL-137's typed soft-reject (`ModelRetry`) is
one path, not a uniform "refusal is data" shape. The operator gateway
returns a structured `{ok, refusal: {reason, detail}}` envelope from
every tool (refusal as model-legible data) and gates stale data behind an
explicit freshness check.

## Decision

New module `harness/freshness.py`, two additive read-side pieces.

- `Refusal`: a small frozen pydantic record (`reason`, `detail`) so a
  tool can return a refusal as *model-legible data* rather than raising
  or returning prose the model misreads. A workload wraps it in its own
  result/output model, the same boundary the degraded disposition
  (ADR 0030) draws between the substrate (which ships the type) and the
  workload (which reports it).
- `require_fresh`: a `Predicate` factory in the `grounding_predicate`
  shape (ADR 0030). The workload supplies an `extract(state) -> datetime`
  returning the value's as-of instant; the predicate passes iff that
  instant is within `max_age_seconds` of the clock. Defaults to
  `Severity.SOFT` (a stale read marks the run degraded, ADR 0030, rather
  than halting); `Severity.HARD` makes a stale read a terminal
  `PostconditionViolation`. The pure `is_stale(as_of, max_age, *, now)`
  helper underlies it.

Freshness is inherently time-dependent, so the clock is injected
(`clock`, default the wall clock) to keep the predicate deterministic
under test, the same stance the bitemporal store (ADR 0032) and the
journal (ADR 0034) take with explicit timestamps. `max_age_seconds` is
validated finite / non-negative at build time (ADR 0007, the BL-159 /
BL-231 class), and naive datetimes are rejected so the subtraction never
raises a naive-vs-aware `TypeError`.

## Scope held out of this change

The full `{ok, value, refusal}` *envelope* type (a generic result
wrapper over every tool return) stays a workload concern: the substrate
ships the `Refusal` data type and the freshness predicate; how a workload
threads them through its tool returns and output model is its
integration, exactly as the degraded disposition's reporting surface is.
A read stamping an `as_of` automatically (rather than the workload
supplying `extract`) would couple the store to a freshness model and is
out of tree.

## Consequences

- No L1 change. Purely additive: a new module, a predicate factory, and a
  data type; a workload that uses neither is unaffected, and the predicate
  defaults to SOFT so it relabels rather than halts.
- Blast radius: `harness` only (`harness/freshness.py` new,
  `harness/__init__.py` exports). No memory, runtime, or schema change.
  Rollback: revert the commit.
- `require_fresh` composes with the contract machinery as an ordinary
  postcondition (or precondition over an input carrying an as-of), so a
  stale read flows through the existing SOFT/HARD severity, recovery, and
  `RunRecord.degraded` paths with no new enforcement code.
- Tests: 14 new cases (`tests/harness/test_bl246_freshness.py`);
  `harness/freshness.py` at 100% line coverage.

## Revisit triggers

- A workload wants the full `{ok, value, refusal}` envelope as an in-tree
  generic type rather than a workload output-model field.
- A memory read that stamps `as_of` itself (coupling a freshness model to
  the store) is needed, beyond the workload-supplied `extract`.
- A guard-level `require_fresh` that turns a stale read into a
  REQUIRE_APPROVAL (rather than a postcondition violation) pairs with the
  authority tiers (ADR 0029).
