# ADR 0022: Twelfth code audit, additive hardening

- Status: Accepted
- Date: 2026-06-01
- Authors: rmednitzer
- Builds on: ADR 0001-0021

## Context

A twelfth in-depth audit of `harness`, `memory`, `skills`,
`workloads`, the CLI, `evaluation`, and the offline gates, run against
the same green gates (ruff, ruff format, mypy strict, pytest at
`cov-fail-under=94`, schema-drift, REUSE 3.x, `pip-audit`, the dispatch
evaluation gate at P@1 = MRR = 1.0).

This audit re-walked one recurring fault class against a family of
surfaces no prior audit had reached: the **non-finite numeric** class.
Every comparison with `NaN` is `False`, and `+inf <= 0` is also
`False`, so a `NaN` (or `+inf`) value silently subverts a numeric
control in one of two ways:

- it makes a `consumed > limit` ceiling check always `False`, so the
  ceiling never trips (the BL-159 cosine / BL-205 weight / BL-221
  consume-cost / BL-226 S3-metadata trap); or
- it slips through a `value <= 0` positivity guard, because both
  `NaN <= 0` and `+inf <= 0` evaluate `False`.

The prior NaN audits all closed this class at the **value / data**
boundaries: a cosine score (BL-159), a `MultiDispatcher` weight
(BL-205), the floats a caller *consumes* into `BudgetTracker`
(BL-221), and a parsed S3 user-metadata string (BL-226). BL-197 was
the one prior fix on a **configuration** boundary
(`Namespace.retention_seconds` validated finite-and-positive at
construction). This audit asked whether the *other* numeric
configuration boundaries, the limits / timeouts / intervals an
operator sets, were brought to the BL-197 standard. They were not.

The audit found two findings, split by sub-mechanism:

- **BL-231 (no guard at all)**: `ActionBudget` (`harness/budgets.py`)
  and `RetryPolicy` (`harness/runtime.py`) had no finiteness / sign
  validation on their numeric fields. A `NaN` / `+inf` budget limit
  makes the tracker's `consumed > limit` check always `False`, so the
  ceiling the operator declared is silently disabled for the whole
  run, the exact dual of BL-221 (which hardened the *consumed* side of
  the same comparison but left the *limit* side open). A `NaN` backoff
  makes `RetryPolicy.delay_for` non-finite, and `asyncio.sleep(NaN)`
  returns immediately, so the bounded exponential backoff degrades to a
  no-delay retry storm against the very provider that is failing.

- **BL-232 (a guard with a `NaN` hole)**:
  `MCPServerSpec.timeout_seconds` (`harness/mcp.py`) and
  `TTLSweeper.interval_seconds` (`memory/sweep.py`) both reject
  non-positive values with `value <= 0`, but `NaN <= 0` and
  `+inf <= 0` are both `False`, so a non-finite value passes a guard
  whose docstring explicitly claims "must be positive". The sweeper
  case is the more acute one: a `NaN` interval drives
  `asyncio.wait_for(self._stop.wait(), timeout=NaN)`, which raises
  `TimeoutError` immediately, turning the maintenance loop into a
  no-delay busy-sweep that hammers the backend's `sweep_expired` as
  fast as the event loop allows.

The recurring lesson holds, and is sharpened: a guard that *looks*
correct (`value <= 0` to enforce "positive") is not correct against a
`NaN` / `+inf` input, because IEEE 754 makes every ordered comparison
with `NaN` false. The four surfaces are the configuration peers of the
`Namespace.retention_seconds` boundary BL-197 already hardened; this
audit brings them to the same standard.

## Decision

### 1. `ActionBudget` numeric-limit validation (BL-231)

A BL-221 dual: BL-221 validated the floats a caller *consumes*; this
validates the *limits* the spec declares.

`ActionBudget` is a frozen Pydantic model whose numeric fields had no
constraints, so `ActionBudget(max_cost_usd=float("nan"))` constructed
cleanly and `BudgetTracker(...).consume_cost(1e9)` never raised: the
`_check` comparison `consumed > float("nan")` is always `False`. The
same held for `max_wall_clock_seconds`, the values of
`max_wall_clock_seconds_per_tool`, and (for `+inf`) any of them. A
negative limit was likewise accepted (a meaningless spec that
fail-trips immediately).

The fix is a `model_validator(mode="after")` calling two module-level
helpers:

```python
def _validate_float_limit(field: str, value: float | None) -> None:
    if value is None:
        return
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite when set (got {value!r})")
    if value < 0:
        raise ValueError(f"{field} must be non-negative when set (got {value!r})")


def _validate_int_limit(field: str, value: int | None) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{field} must be non-negative when set (got {value!r})")
```

applied to every scalar limit and every per-tool map value. Two
deliberate scope decisions:

- **`None` and `0` stay valid.** `None` is the unlimited sentinel; `0`
  is a legitimate zero ceiling (`max_cost_usd=0.0` is exercised by the
  existing budget test: "no spend allowed"). Only `NaN`, `+inf` / `-inf`, and
  negative are rejected. Every existing budget is therefore unaffected;
  this is additive to L1.
- **Validation is at construction, not at `consume`.** A misconfigured
  budget now fails when the spec is built (ADR 0007 "configuration
  errors at load time, not mid-run"), matching the
  `Namespace.resolve_ttl` (BL-197) and `MultiDispatcher` weight
  (BL-205) precedents. A Pydantic `model_validator` raising
  `ValueError` surfaces as the usual `ValidationError`, the same shape
  as `SkillManifest._check_spec`.

This is a runtime validator, not a JSON-Schema constraint, so the
generated `docs/schema/*.json` is unchanged by it (the only schema
diff in this change is the `MCPServerSpec` docstring propagation from
finding 3).

### 2. `RetryPolicy` parameter validation (BL-231)

The same class on the retry-policy config object. `RetryPolicy` is a
frozen dataclass; a `__post_init__` validates:

- `max_retries >= 0` (a negative retry count is meaningless);
- `backoff_base_seconds` / `backoff_max_seconds` finite and `>= 0`
  (a `NaN` makes `delay_for` non-finite; since `min(NaN, deadline)`
  keeps the `NaN` and `asyncio.sleep(NaN)` returns immediately, a `NaN`
  backoff turns the bounded backoff BL-136 promises into a no-delay
  retry storm; `0` is a valid "no delay" choice and stays accepted);
- `circuit_breaker_threshold` is `None` or `>= 1` (the breaker trips
  after that many consecutive failures, so `0` / negative would trip on
  a fresh instance and defeat retries entirely).

Validated at construction, the same ADR 0007 stance.

### 3. `MCPServerSpec.timeout_seconds` `NaN` hole (BL-232)

The existing validator was:

```python
if self.timeout_seconds <= 0:
    raise ValueError("timeout_seconds must be positive")
```

`NaN <= 0` and `+inf <= 0` are both `False`, so a non-finite timeout
passed a guard that claims "must be positive". The fix adds a
`math.isfinite` conjunct:

```python
if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
    raise ValueError("timeout_seconds must be a positive, finite number")
```

The docstring is updated to "a positive, finite number" (this is the
only generated-schema change in the wave: the docstring is the
schema `description`). No valid timeout is newly rejected.

### 4. `TTLSweeper.interval_seconds` `NaN` hole (BL-232)

The `MCPServerSpec` twin, in a plain class rather than a Pydantic
model. The guard was `if interval_seconds <= 0`; the same `NaN` / `+inf`
hole applied, and the consequence is sharper than a bad timeout: the
loop awaits `asyncio.wait_for(self._stop.wait(), timeout=interval)`,
and `asyncio.wait_for(..., timeout=NaN)` raises `TimeoutError`
immediately, so a `NaN` interval makes the sweeper spin with no delay,
calling `sweep_expired` on the backend (Redis / DynamoDB / S3 network
I/O) as fast as the event loop allows, a self-inflicted load storm.
The fix adds the same `math.isfinite` conjunct. `max_keys` is left as
is: it is an `int | None` parameter with no `NaN` representation, and
its `<= 0` guard already rejects the only meaningless integer values.

## Consequences

- The fixes are additive to the L1 Protocols (ADR 0007). No public
  signature changed; no caller behaviour changed on any valid input
  (`None` / `0` / finite-positive). Only a `NaN` / `+inf` / `-inf` / negative
  configuration, which previously constructed cleanly and then
  silently disabled or corrupted a control, is now rejected at the
  construction boundary with a diagnostic naming the field.
- A misconfigured numeric control now **fails closed at load time**
  instead of **failing open at runtime**: a budget ceiling, an MCP
  timeout, a sweep interval, or a retry backoff that an operator
  believes they set can no longer be silently a no-op because a
  computed value resolved to `NaN` / `+inf`.
- The four surfaces are now consistent with the
  `Namespace.retention_seconds` (BL-197) configuration boundary, so a
  backend / harness author copying any one of them as a template sees
  the finiteness check in place rather than the bare `<= 0` hole.
- 39 new regression tests:
  - `tests/harness/test_bl231_bl232_numeric_config.py` (33):
    `ActionBudget` rejects `NaN` / `+inf` / `-inf` / negative on `max_cost_usd`,
    `max_wall_clock_seconds`, the per-tool wall-clock map, and the
    per-tool int maps, rejects negative `max_steps` / `max_tokens` /
    `max_tool_calls`, and still accepts `None` / `0` / finite-positive;
    a pinned demonstration that a `NaN` cost limit would have disabled
    the ceiling and that a finite ceiling still fires. `RetryPolicy`
    rejects `NaN` / `+inf` / `-inf` / negative backoff, negative `max_retries`,
    and `< 1` `circuit_breaker_threshold`, still accepts the documented
    defaults and finite values, and a pinned demonstration that a
    finite backoff yields a usable monotonic delay. `MCPServerSpec`
    rejects `NaN` / `+inf` (the closed hole) and still rejects `0` /
    negative and accepts a positive finite timeout and the default.
  - `tests/memory/test_bl232_sweeper_interval.py` (6): `TTLSweeper`
    rejects `NaN` / `+inf` / `-inf` and still rejects `0` / negative
    and accepts a positive finite interval.
- Coverage stays above the 94% gate.

## Revisit triggers

- **Bare-float control *parameters* (`drift_threshold`, dispatcher
  `threshold`).** `run_under_contract(drift_threshold=...)` compares
  `divergence > drift_threshold`, and the cheap-first dispatchers
  compare `confidence >= threshold`; a `NaN` there silently disables
  the drift *alert* or the chain *short-circuit*. These are
  deliberately left unvalidated in this audit: they are optional
  alerting / cost-optimisation knobs rather than safety/resource
  ceilings (a disabled drift alert loses a signal but changes no run
  outcome; a disabled short-circuit only forces the full, more
  expensive chain, whose degradation is already visible in the
  `InstrumentedDispatcher` `fallback_rate` telemetry), and they are
  bare function / constructor parameters rather than the stored spec
  objects that have a natural single validation point. Bringing them
  under the same finiteness check is a tracked follow-up if the
  scattered per-call validation proves worth it; the higher-severity
  configuration-object surfaces are closed here.
- **Event-model output floats** (`harness/events.py`: `limit`,
  `consumed`, `confidence`, `divergence`, `duration_ms`, `latency_ms`,
  `top_confidence`). Left unvalidated as a deliberate non-finding: they
  are sink-bound observability outputs, not control-flow inputs, and
  every in-tree producer now feeds them from a validated source (the
  budget limit by BL-231, `SkillMatch.confidence` by its `ge=0.0,
  le=1.0` field, the JSD `divergence` by the `max(0.0, min(1.0, ...))`
  clamp that collapses `NaN` to a finite value, the durations by
  wall-clock deltas that are always finite). A `NaN` cannot reach them
  through a validated path, and were one to (a custom out-of-tree
  producer), it would be an audit artifact, not a control bypass.
- The standing open items unchanged by this audit: `BL-120` (live
  reference workload), `BL-132` / `BL-171` (prompt caching),
  `BL-113` / `BL-138` (true OTel spans), `BL-114` (deeper resume),
  `BL-135` open half (compaction / summarisation / tiering),
  `BL-155` (true wall-clock preemption), `BL-179` (`RetryPolicy`
  partial-usage accounting), and the `S3Store._sweep_sync` transient
  (non-not-found) error best-effort question deferred by ADR 0021.
