# ADR 0030: DEGRADED disposition and grounding postconditions (BL-244)

- Status: Accepted
- Date: 2026-06-13
- Authors: rmednitzer
- Builds on: ADR 0002 (behavioural contracts, SOFT vs HARD severity),
  ADR 0012 (run-provenance records)

## Context

The Vertex MCP analysis (`docs/analysis/vertex-mcp-lessons.md`, merged
in #114) found two gaps in how this repo expresses output quality:

1. A run's terminal `RunOutcome` (ADR 0012) is binary at the success
   end: `completed`, or one of the hard-violation / budget terminals.
   There is no value for "the run delivered its output, but a quality
   obligation was not met". A SOFT postcondition violation already lets
   the run continue and ship its output (ADR 0002), but that fact is
   invisible to a downstream consumer of the `RunRecord`: a clean run
   and a run that shipped despite a failed soft check are recorded
   identically.
2. The repo ships no grounding / citation postcondition, despite that
   being the highest-value anti-confabulation check for a retrieval
   agent. The operator gateway's reliability runbook makes exactly this
   a deterministic check ("every cited CVE-YYYY-N must appear in the
   captured tool output") that relabels a run as degraded without
   rewriting the model's content.

The gateway keys this to a three-valued ok / degraded / error
disposition: a degraded run still ships its artifact (exit 0, so a
scheduler does not flap) but carries a banner so a human knows the
output is partial. BL-244 brings that disposition and the grounding
reference in tree, additive to L1 (ADR 0007).

## Decision

Two coupled, additive increments.

### `RunRecord.degraded` (record schema v1.1.0)

`RunRecord` gains an optional `degraded: bool = False` field, and
`RUN_RECORD_SCHEMA_VERSION` advances to `1.1.0` with `1.0.0` kept in
`SUPPORTED_RUN_RECORD_SCHEMA_VERSIONS`. Because the field has a default,
a v1.0.0 record (written without it) and a v1.1.0 record both validate
against the current model, so the offline gate
(`scripts/check_run_records.py`) still validates every supported record
against one model and needs no per-version dispatch yet (the structure
`sentinel`'s `validate_artifacts.py` reaches only once it has more than
one incompatible schema).

`degraded` is the orthogonal *quality* axis, not a new `RunOutcome`
value: `outcome` stays `completed` (the run did not halt), and
`degraded` records whether a SOFT postcondition was violated on the
final delivered leg. Keeping the two axes separate (rather than adding a
`degraded` member to the `RunOutcome` Literal) preserves the ADR 0012
lockstep between `RunOutcome` and
`evaluation.dataset.TrajectoryOutcome`, and keeps every existing
outcome-matching call site unchanged.

`run_under_contract` sets it: a per-leg `leg_soft_failed` flag is raised
the first time a SOFT postcondition is violated on a leg, reset at the
start of each leg (so a retry that recovers clears it), and captured
into the terminal `completed` record at the no-retry break. Every other
terminal (`postcondition`, `paused`, `budget`, `governance`,
`approval_denied`, `output_invalid`, `precondition`, `invariant`) emits
`degraded` at its `False` default, so the flag is only ever non-default
on a `completed` run.

That structural half of the guarantee (`degraded` False on any
non-`completed` outcome) is contract-independent, so
`record_invariant_violations` (shared by `verify_run_record` and the
offline `scripts/check_run_records.py` gate) enforces it: a malformed or
buggy producer that stamps `degraded=True` on, say, a `budget` terminal
is rejected by both paths. The other half (no SOFT postcondition failed)
needs the contract and the output, so it stays a producer guarantee
rather than a gate check.

### `harness/grounding.py`

A new module with the deterministic anti-confabulation core:

- `ungrounded_citations(claim, sources, *, pattern) -> list[str]`: a
  pure function returning the citation tokens (regex matches of
  `pattern`, e.g. `r"CVE-\d{4}-\d{4,}"`) in `claim` that do not appear
  as a substring of `sources`, in first-appearance order, deduplicated.
  An empty list means every cited token is grounded; a claim with no
  matches is vacuously grounded.
- `grounding_predicate(extract, *, pattern, name=, severity=SOFT)`: a
  `Predicate` factory. `extract(state) -> (claim, sources)` is
  workload-supplied (the framework binds no output-model shape, the
  ADR 0001 `Embedder` / `TierClassifier` injection stance). The
  predicate passes iff `ungrounded_citations` is empty. It defaults to
  `Severity.SOFT`, so an ungrounded output marks the run degraded
  without halting it; `Severity.HARD` makes ungrounded output a
  terminal `PostconditionViolation` instead.

This relabels without rewriting: the check is deterministic and never
touches the model's content.

### Disposition of the recovery directives (ADR 0002 / BL-102)

A SOFT postcondition can carry a `RecoveryOutcome.directive`. The
`degraded` flag composes with each:

- `continue` (the emit-and-continue default): degraded.
- `retry` that recovers: the fresh leg resets `leg_soft_failed`, so a
  clean retry leg is NOT degraded; an exhausted retry that still fails
  soft-continues and IS degraded.
- `escalate`: raises `PostconditionViolation`; the terminal is
  `postcondition`, not `completed`, so `degraded` keeps its `False`
  default.
- `substitute`: the substituted output is NOT re-validated against the
  postconditions, and a soft violation did occur on the delivered leg,
  so the honest disposition is degraded (a "substitute re-validates and
  may clear degraded" option is a revisit trigger below, not this
  increment).

## Scope held out of this change

- The richer ok / degraded / error *reporting* surface (a banner on the
  workload's output model, a blocker event, scheduler exit-code
  conventions) is a workload concern: the substrate records the
  disposition; how a given workload surfaces it is out of tree, the same
  boundary the gateway draws between its runbook and its scheduler.
- Per-version `RunRecord` validator dispatch in the offline gate stays
  unbuilt until a non-additive schema change forces it (above).

## Consequences

- No L1 change. With no grounding postcondition and no SOFT
  postcondition violation, `degraded` is its `False` default on every
  record and `grounding.py` is unused; every existing call site is
  unchanged. The field, the module, and the schema bump are additive.
- Blast radius: `harness` only. `harness/grounding.py` (new),
  `harness/provenance.py` (the `degraded` field + schema bump),
  `harness/enforcement.py` (the `leg_soft_failed` / `degraded_run`
  threading and the `_emit_record` keyword), `harness/__init__.py`
  (exports), `docs/schema/run-record.json` (regenerated). No runtime,
  memory, or contract-surface change. Rollback: revert the commit (a
  persisted v1.1.0 record then fails the supported-version check on the
  reverted build, the intended behaviour for a downgrade).
- The record schema change is forward-compatible: pydantic ignores
  unknown fields by default, so a reader on the prior model reads a
  v1.1.0 record (dropping `degraded`), and the current model reads a
  v1.0.0 record (the field defaults).
- Tests: 20 new cases
  (`tests/harness/test_bl244_degraded_and_grounding.py`);
  `harness/grounding.py` at 100% line coverage.

## Revisit triggers

- A `substitute` recovery handler that wants its replacement
  re-validated (and `degraded` cleared when the replacement is clean):
  the loop would re-enter the postcondition pass on the substituted
  output rather than `continue` past it.
- A grounding check that needs token *normalisation* (case-folding,
  whitespace, `CVE` vs `cve`) before the substring test: the reference
  is deliberately exact-substring; a normalising variant is a new
  predicate beside it.
- A non-additive `RunRecord` schema change: build the per-version
  validator dispatch in the offline gate (the deferred structure above).
