# ADR 0019: Ninth code audit, additive hardening

- Status: Accepted
- Date: 2026-05-24
- Authors: rmednitzer
- Builds on: ADR 0001-0018

## Context

A ninth full in-depth audit of `harness`, `memory`, `skills`,
`workloads`, the CLI, `evaluation`, and the offline gates, run against
the same green gates (ruff, ruff format, mypy strict, pytest at
`cov-fail-under=94`, schema-drift, REUSE 3.x, `pip-audit`, the
dispatch evaluation gate at P@1 = MRR = 1.0). The prior audits (ADR
0009 / 0010 / 0011 / 0013 / 0015 / 0017 / 0018) and the BL-180 /
BL-133 / BL-212-BL-214 capability waves closed a wide surface; this
audit re-walked the same *classes* the prior audits fixed pointwise,
with particular attention to the BL-222 (`MultiDispatcher` per-member
failure containment) fix from ADR 0018 and whether the same
"fan-out failure must not poison surviving members" class generalises
to other fan-out code paths in the tree.

The audit found one new previously-untracked issue in `harness/`
(`sinks.py` per-sink failure on the audit fan-out side). The finding
is a class extension of BL-222 from the dispatcher ensemble side to
the audit-sink fan-out side: BL-222 made `MultiDispatcher`'s member
failures observable without poisoning surviving members; BL-223 makes
`MultiSink`'s per-sink failures observable without poisoning the
delivery to surviving sinks. Consistent with the prior audits, the
clear bug is fixed additively with a regression test in the same
increment; this ADR records the cross-cutting reasoning. The backlog
tracks the line item (`BL-223`).

The recurring lesson, again: every new fan-out path the codebase
adds is a re-instance of an invariant a prior audit generalised on a
neighbouring surface. The audit's job is to verify the class
generalises to every fan-out point, not only the most recently fixed
one.

## Decision

### 1. MultiSink per-sink failure containment (BL-223)

A BL-222 class extension on the audit fan-out side.
`harness.sinks.MultiSink.emit` iterated its wrapped sinks and called
``sink.emit(event)`` without any exception containment. A single sink
raising (a flaky OTel exporter, a disk-full `JsonlSink`, any sink
with a transient network or filesystem error) crashed the fan-out
loop, so every downstream sink in the `MultiSink` was skipped for
that event.

The downstream consequence is the BL-202 / BL-167 audit-vs-raise
parity invariant ("every state-affecting raise has a matching audit
event") broken at the fan-out boundary: the enforcement loop's
`active_sink.emit(BudgetExceededEvent(...))` or
`active_sink.emit(GovernanceViolated(...))` could be lost on the
OTLP sink because the in-process JsonlSink happened to fail first,
or vice versa. The bare `raise BudgetExceeded(...)` then arrived in
the caller without a matching event in the downstream-of-the-failure
sinks: exactly the audit-vs-raise gap that motivated `BL-202`
(`emit_wall_clock_exceeded`) on the boundary instant.

BL-222 (eighth audit) made `MultiDispatcher`'s ensemble *robust to
per-member failure*: ``return_exceptions=True`` plus an
"exception-to-empty-list" aggregation step so a single failing inner
does not cancel siblings or distort the blend. BL-223 is the dual
guarantee on the audit-sink side: a single failing sink must not
prevent delivery to the surviving sinks.

The fix wraps each ``sink.emit(event)`` call in a per-sink
``try/except Exception`` and continues on failure.
``BaseException`` (`KeyboardInterrupt`, `SystemExit`,
``asyncio.CancelledError``) is deliberately *not* contained: those
are authoritative termination signals, parity with the runtime's
`BL-165` "do not reinterpret cancellation as a pause" invariant.

Seven regression tests pin the boundary: a failing middle sink does
not block downstream sinks; a failing first sink does not block
subsequent sinks; the all-failing case returns cleanly (so the
caller is not aborted by an audit-pipeline failure); a
``BaseException`` (``KeyboardInterrupt``) still propagates; the
happy-path fan-out is byte-for-byte the prior behaviour; an empty
fan-out is still a no-op; and a multi-event sequence with one
intermittent failing sink delivers every healthy event to every
healthy sink in order.

## Consequences

- The fix is additive to the L1 Protocols (ADR 0007). No public
  signature changed; no caller behaviour changed on the happy
  path. The exception types still propagating on the fan-out
  boundary (`BaseException`) are existing types, not new ones, so
  a caller that catches `KeyboardInterrupt` / `SystemExit` /
  `CancelledError` gains the same precision the prior code
  provided.
- 7 new regression tests
  (`tests/harness/test_bl223_multi_sink_failure.py`).
- Coverage stays at 94.94% (above the 94% gate; the absolute number
  fluctuates with pytest discovery and the new tests, the gate is
  what matters).
- The audit fan-out boundary (`harness/sinks.py`) is now consistent
  with the BL-222 ensemble-side standard: per-member failure
  containment with `BaseException` propagation, on both the
  parallel dispatcher fan-out and the sequential sink fan-out.
- An operator pairing a local `JsonlSink` with a network-backed
  `OTelSink` inside a `MultiSink` no longer loses one sink's
  delivery to the other's transient failure. The audit-vs-raise
  parity invariant is upheld across every fan-out leg, not only
  the legs whose downstream happens to be reached before the
  first failure.

## Revisit triggers

The open items this audit deliberately did not touch:

- `BL-120` (live reference workload). Needs a funded provider key.
- `BL-132` / `BL-171` (prompt caching on the runtime adapter).
  Upstream-dependent on a verified PydanticAI provider-cache API
  plus a live model to validate.
- `BL-113` / `BL-138` (true OTel spans + GenAI semantic
  conventions). Upstream-dependent on the OTel logs SDK GA.
- `BL-114` (deeper PydanticAI resume). Upstream-dependent.
- `BL-135` open half (compaction / summarisation / tiering, plus
  `BoundedSweepableStore` on `DynamoDBStore` / `S3Store`).
  In-tree work; the size-bound on the remaining two durable
  adapters needs an auxiliary timestamp attribute (DynamoDB) or
  timestamp-prefixed object (S3) since neither has the native
  ordering SQLite (rowid) or Redis (sorted set) provide.
- `BL-150` (commit-SHA pinning of GitHub Actions). Maintainer or
  Dependabot action.
- `BL-155` (true wall-clock preemption). Needs a thread/process
  execution boundary; the asyncio `await`-based watchdog is the
  documented preempt-at-yield-point shape.
- `BL-179` (`RetryPolicy` partial usage accounting). Upstream-
  dependent on PydanticAI exposing partial usage on exception.
- `RoutingChainDispatcher` per-link exception fall-through. The
  chain is a documented fallback chain ("tries dispatchers in
  order until one returns a match above threshold"; "best-effort
  fallback: returns last non-empty result"); a link that *raises*
  could be treated like a link that *returns empty* and the chain
  could fall through to the next link. This is a class extension
  of BL-222 / BL-223 on the sequential side, but distinguishing
  a transient inner failure from a terminal `BudgetExceeded` /
  `GovernanceViolation` (both `Exception` subclasses) needs
  selective catching, not a blanket containment. Deferred to a
  future audit pending a clear semantic for which exception
  classes constitute "fall through" vs "propagate" on the chain
  boundary.
