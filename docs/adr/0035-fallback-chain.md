# ADR 0035: Graceful degradation ladder around the Runtime (BL-248)

- Status: Accepted
- Date: 2026-06-13
- Authors: rmednitzer
- Builds on: ADR 0003 (budgets, the `Runtime` Protocol, `RetryPolicy`)

## Context

`RetryPolicy` (BL-136) retries the *same* call against the *same*
provider with backoff and a circuit breaker, which is resilience against
a transient blip. It is not a fallback ladder: when a provider is down
(not merely flaky), or a premium model is unavailable, the
operator-gateway pattern *degrades* to the next option (a cheaper model,
a cached path, a local stub) so the pipeline returns an answer rather
than failing. The repo had no first-class way to express that ordered
descent.

## Decision

New module `harness/fallback.py`: `FallbackChain`, a `Runtime` that wraps
an ordered list of `Runtime`s and tries each in turn until one returns.
It composes with `RetryPolicy` rather than replacing it: each member
runtime owns its own retry/backoff, and the chain descends only once a
member has exhausted its own resilience and raised.

The descend boundary is deliberate. A `should_descend(exc)` predicate
decides whether to fall through; the default (`default_should_descend`)
descends on any `Exception` that is *not* a `HarnessError`. A deliberate
policy halt (governance reject, budget exceeded, approval denied, all
`HarnessError` subclasses) therefore never reroutes onto a backup
provider, so the chain cannot launder a governed-away or budget-exceeded
call. A `BaseException` (`KeyboardInterrupt` / `SystemExit` /
`CancelledError`) always propagates (the BL-165 invariant). An approval
pause is a `ResumableState` return value, not an exception, so it is
returned as-is and never triggers a fallback.

`FallbackChain` is a per-run composition, not a contract change:
`run_under_contract` already takes any `Runtime`, so a chain drops in
unchanged. `stream` delegates to the first member only (a partially
streamed response cannot be cleanly retried on another provider),
documented rather than faked.

## Consequences

- No L1 change. Purely additive: a new module and a new `Runtime`
  composition; nothing existing changes, and a workload that does not use
  it is unaffected.
- Blast radius: `harness` only (`harness/fallback.py` new,
  `harness/__init__.py` exports). No runtime-adapter, memory, or schema
  change. Rollback: revert the commit.
- The same `budget` (and every kwarg) is threaded to each attempt, so a
  failed attempt's spend still counts against the run budget. A caller
  who wants per-provider budgets composes differently (separate runs).
- Tests: 13 new cases (`tests/harness/test_bl248_fallback.py`);
  `harness/fallback.py` at 100% line coverage.

## Revisit triggers

- A workload needs mid-stream fallback (today `stream` has none).
- A workload needs per-member budgets or a circuit breaker *across* the
  chain (today each member's `RetryPolicy` is independent and the budget
  is shared).
- A descend decision that depends on the partial result, not just the
  exception (a returned low-confidence answer triggering a descent): the
  predicate is over exceptions only by design.
