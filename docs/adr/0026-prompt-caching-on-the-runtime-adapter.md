# ADR 0026: Prompt caching on the runtime adapter

- Status: Accepted
- Date: 2026-06-12
- Authors: rmednitzer (drafted by the 2026-06-12 Claude Code backlog session)
- Builds on: ADR 0001, ADR 0003, ADR 0007, ADR 0010 (BL-123), ADR 0012, ADR 0025

## Context

`BL-132` / `BL-171` tracked prompt and response caching on the
runtime adapter: cache-breakpoint control for the stable tools/system
prefix, and surfacing the provider's
`cache_creation_input_tokens` / `cache_read_input_tokens` so cost
accounting (`BL-123`) can price them. Both items were deliberately
deferred ("tracked, not rushed", runbook section 4.3) on two
conditions: a verified PydanticAI provider-cache API, and a live
model to validate. Shipping a no-op flag would have breached the
no-half-implementation bar.

The first condition has now been met and was verified in-session
against the locked dependency set: pydantic-ai 1.106.0 ships
`CachePoint` (an explicit cache-breakpoint message part), the
`AnthropicModelSettings` cache controls
(`anthropic_cache_instructions`, `anthropic_cache_tool_definitions`,
`anthropic_cache_messages`, `anthropic_cache`), and
`RunUsage.cache_read_tokens` / `cache_write_tokens` (the upstream
mapping of Anthropic's `cache_read_input_tokens` /
`cache_creation_input_tokens`; the installed Anthropic model module
keeps these outside `input_tokens`, matching the provider's own
accounting). `Agent` accepts `model_settings`, and
`FunctionModel.AgentInfo` exposes the settings the model call
received, so the pass-through is deterministically testable
end to end.

The second condition (a live model) is still not met in this
environment: no provider key exists, and `BL-120` (the credentialed
live-workload smoke) remains open. The maintainer chose to land the
wiring now with deterministic validation, and to couple the live
cache-hit validation to `BL-120` explicitly rather than leave the
whole capability blocked on it.

## Decision

### 1. `model_settings` pass-through, not a cache-specific surface

`PydanticAIRuntime` gains one optional keyword,
`model_settings: Any | None = None`, forwarded verbatim to the
underlying `Agent` in `_build_agent`. `None` preserves the prior
construction exactly (ADR 0007).

A dedicated `PromptCacheSpec` was considered and rejected: it would
re-model a vendor-specific surface the upstream already types
(`AnthropicModelSettings`), bind the harness to one provider's cache
semantics (against ADR 0001), and lag upstream churn. Treating the
settings as opaque, exactly like `model`, keeps the adapter
vendor-neutral: an Anthropic workload passes the Anthropic settings,
any other provider passes its own, and `CachePoint` message parts
remain available to workloads that build messages directly.

### 2. Cache-token surfacing through the tracker, no new ceiling

`BudgetTracker` gains `consume_cache_tokens(*, read=0, write=0)` and
the readable counters `cache_read_tokens` / `cache_write_tokens`.
The adapter surfaces the counts in `run()` after success and in
`stream()` at the final reconciliation (providers finalize cache
accounting with run-level usage, not per chunk), getattr-guarded so
a usage object without the fields (an older PydanticAI, a custom
double) is a silent no-op, the `_usage` compat stance.

Three deliberate boundaries:

- **Not charged to `max_tokens`.** Upstream reports cache counts
  outside `input_tokens`, so charging them would double-count
  against the provider's own token accounting and change the
  meaning of every existing budget. The `tokens` dimension stays
  `input + output`, byte-identical to before.
- **No cache ceiling.** Surfacing is pure accounting. Pricing cache
  reads/writes is provider-specific (Anthropic discounts reads,
  surcharges writes), so the framework surfaces counts and a
  pricing-aware caller feeds spend through `consume_cost`, the
  established BL-123 caller-fed stance. A `max_cache_*` limit can be
  added later without breaking this surface.
- **Not in `snapshot()`.** BL-154 carries enforced dimensions across
  an approval pause; there is no cache ceiling to carry. The
  snapshot keys are regression-pinned by a test so the resume
  surface cannot grow by accident.

Negative counts are rejected at the entry boundary (the BL-221
caller-fed input class; ints cannot be NaN, so only sign needs
guarding).

### 3. Live validation stays coupled to `BL-120`

What the deterministic suite proves: the settings reach the model
call (run and stream modes), the counts reach the tracker, the
`max_tokens` semantics are unchanged in both directions, and absent
fields are tolerated. What it cannot prove: that a live provider
serves a cache hit, at what discount, and that the breakpoints land
on the intended prefix. That residual is now an explicit part of
`BL-120`'s definition and of `LIMITATIONS.md` L9 (retitled from "No
prompt caching" to the validated-deterministically-only residual).
Cost projections derived from the new counters are unvalidated until
then.

## Consequences

- Additive to L1 (ADR 0007): one new optional keyword with a
  behaviour-preserving default, one new tracker method, two new
  read-only properties. No existing signature, event, or schema
  changed; the resume surface (`snapshot()`) is unchanged and
  test-pinned.
- `BL-132` and `BL-171` move to resolved; `BL-120` gains the live
  cache-hit validation as part of its scope. The runbook's
  "tracked, not rushed" set shrinks to
  `BL-113`/`BL-138`, `BL-114`, `BL-155`, `BL-179`.
- 16 new deterministic tests
  (`tests/harness/test_bl132_prompt_caching.py`); the full suite
  passes with coverage above the 94 % gate.
- A workload opting in pays nothing when the provider ignores the
  settings; the failure mode of a typo in a settings dict is the
  upstream's (PydanticAI/provider) validation, not a silent harness
  no-op, because the value is forwarded verbatim.

## Revisit triggers

- `BL-120` lands: add the live cache-hit assertions (a second run
  with an identical prefix reports `cache_read_tokens > 0`) to the
  credentialed smoke, then close the L9 residual.
- A second provider's cache controls appear in PydanticAI model
  settings: nothing to do here (the pass-through is provider-blind),
  but `docs/runtime-providers.md`'s example section gains the second
  provider.
- A cache-cost ceiling need appears: add `max_cache_read_tokens` /
  `max_cache_write_tokens` to `ActionBudget` beside the existing
  dimensions; the counters and the consume method are already in
  place.
- Upstream starts folding cache counts into `input_tokens` (a
  semantic break): the surfacing helper and the token charge in
  `run()` / `stream()` must be reconciled in the same change; the
  `max_tokens` isolation tests will fail loudly if this lands
  unnoticed.
