# ADR 0018: Eighth code audit, additive hardening

- Status: Accepted
- Date: 2026-05-24
- Authors: rmednitzer
- Builds on: ADR 0001-0017

## Context

An eighth full in-depth audit of `harness`, `memory`, `skills`,
`workloads`, the CLI, `evaluation`, and the offline gates, run against
the same green gates (ruff, ruff format, mypy strict, pytest at
`cov-fail-under=94`, schema-drift, REUSE 3.x, `pip-audit`, the
dispatch evaluation gate at P@1 = MRR = 1.0). The prior audits (ADR
0009 / 0010 / 0011 / 0013 / 0015 / 0017) and the BL-180 / BL-133 /
BL-212-BL-214 capability waves closed a wide surface; this audit
re-walked the same *classes* the prior audits fixed pointwise and the
code paths exercised by the BL-212-BL-214 sweeper-size-bound wave plus
the ADR 0016 (`BL-133`) IPC surface.

The audit found four new previously-untracked issues spread across
`harness/` (two: `sinks.py` write-side encoding, `budgets.py`
caller-fed float trust boundary) and `skills/` (two: subprocess
child-side partial-header handling, MultiDispatcher member-failure
robustness). All four are class extensions of bugs the prior audits
fixed elsewhere: the project's lesson, again, that every boundary the
codebase adds is a re-instance of an invariant the earlier audits
generalised. Consistent with the prior audits, every clear bug is
fixed additively with a regression test in the same increment; this
ADR records the cross-cutting reasoning. The backlog tracks the line
items (`BL-219` through `BL-222`).

## Decision

### 1. JsonlSink UTF-8 encoding (BL-219)

A BL-218 class extension on the write side. `harness.sinks.JsonlSink`
opened the target file with `Path.open("a")` and no explicit
`encoding=`. On a non-UTF-8 platform locale (Windows cp1252, C locale
ASCII), the platform default would apply: a non-ASCII event payload (a
localised error message, a unicode prompt template, a redacted span
carrying high bytes) would either raise `UnicodeEncodeError` past the
documented sink boundary or silently mis-encode the JSONL row,
corrupting the audit stream.

BL-218 fixed the read side (`Path.read_text` consistency across the
workload loader, the golden-set loader, the example workload); the
write side is the natural counterpart, and the same project standard
applies: every file I/O explicitly pins UTF-8. The fix is a one-line
addition of `encoding="utf-8"` to the `Path.open("a", ...)` call. Two
regression tests pin the boundary: one writes an event with non-ASCII
content (accented characters in `workload` / `contract` / `trace_id` /
`span_id`) and confirms the bytes are valid UTF-8 on disk, the other
appends three events and round-trips them through `Path.read_text(
encoding="utf-8")` to confirm multi-line append preserves the
encoding.

### 2. Subprocess child-side partial IPC header (BL-220)

A class extension of `BL-216` on the child side. ADR 0017's BL-216
capped the IPC frame body length at 64 MiB on both the parent and the
child, and handled the *empty-header* and *oversize-header* cases on
both sides. The child's `_read_frame` left one truncated-header case
unhandled: a header with 1, 2, or 3 bytes (parent crashed mid-write
after sending part of the 4-byte length prefix) reached
`_FRAME_LEN.unpack(header)` and raised `struct.error`, crashing the
child with an unhandled exception instead of the clean main-loop exit
the empty-header path takes.

The parent's reciprocal handling in `skills.execution._read_frame`
explicitly checks `len(header) != _FRAME_LEN.size` and raises
`SkillContractExecutorError` at the documented exception boundary
(introduced in BL-216); the child needed the same shape. The fix
mirrors the empty-header `return None` (treat as EOF): a partial
header is a clean EOF on the trusted-input side. The child has no
useful structured-error frame to emit (the parent has either crashed
or bug-emitted a bad header; either way the parent will see EOF on
the child's stdout and raise `SkillContractExecutorError`), so EOF is
the right answer. Five regression tests pin the 1 / 2 / 3-byte
partial-header cases plus the BL-216 empty-header sanity and the
happy-path frame decode.

### 3. NaN/inf at the caller-fed float trust boundary on the budget (BL-221)

A BL-159 / BL-205 class extension on the budget input boundary.
`BudgetTracker.consume_cost(usd: float)` and
`BudgetTracker.consume_tool_call(..., wall_clock_seconds: float)` are
the two caller-fed float surfaces on the tracker (cost is pricing-
aware-adapter-fed, per-tool wall-clock is feed-by-adapter when the
default `PydanticAIRuntime` measures tool body duration via
`time.perf_counter`). Both accepted any `float`, including NaN and
infinity. The class is the BL-159 NaN-clamp trap on a different
surface:

- NaN is *truthy* in Python (so the `consume_cost`'s `if usd:`
  short-circuit does NOT skip a NaN call).
- NaN propagates through `+` (so the accumulator becomes NaN for the
  rest of the run after a single NaN report).
- `NaN > limit` is always `False` (so the `_check` strict-greater
  comparison never trips, regardless of the configured ceiling).

Net effect: a single NaN cost report (a buggy pricing helper, a
misconfigured adapter that emits NaN on a zero-token request) silently
disables the cost ceiling for the entire run. The same shape applies
to `wall_clock_seconds` and the per-tool wall-clock cap.

The fix validates `math.isfinite(...)` and non-negativity at both
entry points, raising `ValueError` with a diagnostic naming the
argument. The validation runs before any state mutation so a rejected
call leaves the tracker exactly as before. Nine regression tests pin
each reject case (NaN, +inf, -inf, negative) on both entry points,
plus the happy path (zero, positive finite). The same approach the
sixth-audit BL-205 took on `MultiDispatcher` weights at construction:
validate at the API boundary so a configuration bug surfaces here
rather than disabling the cap in production.

### 4. MultiDispatcher member-failure robustness (BL-222)

A class extension of BL-207 / BL-208 (InstrumentedDispatcher
telemetry on failure) on the ensemble side. `MultiDispatcher.dispatch`
called `asyncio.gather(*member_dispatches)` without
`return_exceptions=True`. The default asyncio behaviour cancels every
sibling task on the first exception, so:

- A single flaky member (an LLM-backed inner that raises
  `DispatchError` on a malformed response, an embedding provider that
  times out, any member with an upstream failure mode) crashed the
  entire ensemble's `dispatch` call.
- As a secondary effect, the cancelled siblings' `InstrumentedDispatcher`
  `try/finally` wrappers (BL-207) then emitted
  `fell_back=True / matched=0` events. Cancellation is recorded
  indistinguishably from a real fallback, polluting the routing-health
  telemetry signal a workload uses to detect a real degradation.

BL-207 / BL-208 made the InstrumentedDispatcher *observable on
failure*; the ensemble's robustness is the dual of that guarantee. A
member that fails should not poison the surviving members' truthful
contributions or their telemetry.

The fix is `return_exceptions=True` on the `gather` call plus a skip
in the aggregation loop: an `Exception` member result becomes an empty
match list, contributing 0 to the AVERAGE / WEIGHTED / VOTE blend
(parity with the documented "a member that did not return the skill
contributes 0" semantic). The exception is contained at the ensemble
boundary, not propagated past it. The surviving members run to
completion (siblings are not cancelled), so their
InstrumentedDispatcher wrappers record truthful per-member
observability. Four regression tests pin the one-failing-member case
(ensemble survives, ranking unchanged), the AVERAGE-with-failure
contribution (failure = 0/n, not 1/n), the all-fail case (returns
empty), and the happy-path sanity (byte-for-byte the same blend).

## Consequences

- Every fix is additive to the L1 Protocols (ADR 0007). No public
  signature changed on the happy path; no caller behaviour changed on
  the happy path. The exception types raised on the documented
  boundary (`ValueError` from `BudgetTracker` matches its existing
  `ValueError`-on-construction-error idiom; the rest reuse existing
  exception types and EOF semantics) are existing types, not new
  ones, so callers that already catch them gain the new precision
  without code change.
- 20 new regression tests
  (`tests/harness/test_bl219_bl221_audit8.py` with 11 tests,
  `tests/skills/test_bl220_executor_child_partial_header.py` with 5
  tests, `tests/skills/test_bl222_multi_member_failure.py` with 4
  tests).
- Coverage stays at 94.98% (above the 94% gate, up from 94.97%).
- The audit trail surface (`harness/sinks.py`) is now consistent with
  the BL-218 read-side standard: every file I/O in the project pins
  `encoding="utf-8"` explicitly.
- The budget input boundary is hardened to the same "non-finite
  numeric coercion must not silently disable a downstream check"
  invariant the sixth audit's BL-205 / BL-159 closed on the
  MultiDispatcher / cosine_similarity / LLMDispatcher /
  SkillBasedDispatcher boundaries.
- The MultiDispatcher ensemble is now robust against per-member
  failure, so an operator running a multi-member chain over an
  LLM-backed inner does not lose all routing on a single transient
  upstream failure. The cancellation-as-fallback telemetry artefact
  is fixed by removing the cancellation (siblings now complete).

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
- BL-214 atomicity (write + index ZADD aren't atomic) and BL-214
  chunked sweep (`_members()` does `ZRANGE 0 -1`). Both flagged
  by PR #60 review; both tracked under `BL-135`'s remainder
  because the fix (Lua script for atomicity, ZSCAN-style chunked
  iteration) is a follow-up design pass, not a same-PR fix.
