# ADR 0015: Sixth code audit, additive hardening

- Status: Accepted
- Date: 2026-05-23
- Authors: rmednitzer
- Builds on: ADR 0001-0014

## Context

A sixth full in-depth audit of `harness`, `memory`, `skills`,
`workloads`, the CLI, `evaluation`, and the offline gates, run against
the same green gates (ruff, mypy strict, pytest at
`cov-fail-under=94`, schema-drift, REUSE 3.x, the dispatch evaluation
gate). The prior audits (ADR 0009-0013) closed a large surface; the
post-audit BL-195 / BL-196 consolidations on `memory/` cleaned the
expiry boundary class and the encryption-legacy-migration class. This
pass targeted the *classes* the prior audits fixed pointwise and the
code paths exercised by the recent BL-180 / BL-195 / BL-196 work plus
the runbook 7.4 candidates that the consolidation cycle did not close.

The audit confirmed the prior fixes hold and found twelve new,
previously untracked issues spread across all three layers
(`memory/`, `harness/`, `skills/` + `evaluation/`). Consistent with
ADR 0009-0013, every clear bug is fixed additively with a regression
test in the same increment; this ADR records the cross-cutting
reasoning. The backlog tracks the line items (`BL-197`-`BL-208`); the
same ID discipline applies.

The recurring lesson, again: prior audits closed a fault *class* at
the call sites they inspected, not as an invariant the next adapter
or dispatcher inherits by construction. This pass closes twelve more
instances of those same classes (boundary inclusivity, audit-vs-raise
parity, diagnostic gap on failure, NaN-clamp trap, listing-vs-read
parity, empty-batch waste, untrusted-input recursion, audit event
ordering) and consolidates the TTL resolver across the memory
adapters so the validation has one source.

## Decision

### 1. Namespace TTL validation + resolve_ttl consolidation (BL-197)

`Namespace.retention_seconds` accepted NaN and +inf because
`nan <= 0` and `inf <= 0` evaluate False; the BL-195 helpers then
treated `expires_at = now + NaN/inf` as never-expired (the
bug-for-bug Copilot-flagged preservation on PR #51). The constructor
now rejects non-finite values with a clear ValueError naming the
BL-195 helpers as the reason. The same validation applies to the
per-call `ttl_seconds` via a new `Namespace.resolve_ttl(ttl_seconds)`
method that merges the five-way `_ttl` / `_effective_ttl`
duplication across the adapters; each adapter's helper now delegates
to `resolve_ttl`. This closes the BL-195 Copilot follow-up at the
API boundary and consolidates runbook 7.4 candidate "five copies of
the TTL resolver" in one diff (M5 from the audit triage).

### 2. RedisStore.mset empty-batch short-circuit (BL-198)

A BL-178 class extension: SQLiteStore.mset short-circuits on an
empty batch (BL-178) and RedisStore.mdelete short-circuits, but
RedisStore.mset still created a `pipeline(transaction=False)` and
called `pipe.execute()` for no work. A `not items: return` guard
restores parity with the other two paths.

### 3. TTLSweeper failure resilience (BL-199)

A diagnostic-gap (BL-189 class extension) finding: `_run`'s
`await self._store.sweep_expired()` had no exception handling, so a
transient backend error (network blip on a Redis-backed Sweepable,
DynamoDB throttling, S3 hiccup) propagated out of the loop, the
asyncio Task completed with an unretrieved exception, and the
sweeper was silently dead for the rest of the process lifetime. The
loop now catches `Exception` (re-raising `CancelledError` so
`aclose` still works), records the failure on `failures_total` /
`last_error`, and retries at the next interval. `failures_total`
resets to zero on the next successful sweep so a transient blip
self-heals without manual intervention.

### 4. Redactor recursion cap (BL-200)

`Redactor._scrub` walks `dict` / `list` / `tuple` / `set` /
`frozenset` with no depth bound and no cycle detection; a cyclic or
pathologically deep payload (e.g., a workload's `state_snapshot`
carrying a self-referential dict) crashed the redactor with
`RecursionError`, killing the emit chain and silently dropping the
event. A `max_depth: int = 64` field on `Redactor` caps the
recursion: an over-deep container is replaced with the placeholder.
Cycles naturally exceed the depth so the cap also covers cycle
detection without a separate visited set. The
audit-path-must-not-crash stance from BL-167 now extends to the
redaction leg.

### 5. OpenAI batch non-dict line (BL-201)

A BL-189 class extension: `_decode_lines` yielded `json.loads(line)`
directly to a consumer that called `line.get(...)`, so a row that
decoded to bare `null` / a number / a string / an array crashed
the iteration with `AttributeError` and silently dropped every
subsequent row. The decoder now wraps malformed rows
(JSONDecodeError or non-dict) in a placeholder dict
`{"_malformed": True, "_raw": ...}` that lands in the consumer's
existing errored-row branch with a `http_None` diagnostic and the
raw text. Same shape as the BL-189 per-row `error` half of this
function, untreated for the per-line outer object until now.

### 6. Wall-clock boundary BudgetExceededEvent parity (BL-202)

A BL-189 / BL-167 class extension: the runtime's outer loop
(`runtime.py:553-558`) used `if remaining <= 0:` while
`BudgetTracker._check` uses `if consumed > float(limit):` (strict),
so at the exact boundary instant the tracker's `check_wall_clock()`
returned silently and the immediately-following bare
`raise BudgetExceeded(...)` fired without a `BudgetExceededEvent` in
the audit stream. The same shape repeats in `_with_watchdog`. A new
`BudgetTracker.emit_wall_clock_exceeded(elapsed)` method emits the
event with the elapsed time; the runtime calls it in both fallback
paths before the bare raise. Every wall-clock terminal raise now
pairs with an audit event.

### 7. ContractStarted orphan on resume-validation (BL-203)

A BL-167 class extension: `run_under_contract` emitted
`ContractStarted` at line 181 (pre-fix), then validated the resume
state's pending approvals at line 268-271 and raised
`ValueError(...)` on unresolved approvals -- without calling
`_emit_record` (no `RunRecord` to the `record_sink`) and without
emitting `ContractCompleted`. The audit stream and the
run-provenance gate (`scripts/check_run_records.py`) showed an
orphan `ContractStarted` with no terminal partner. The resume
validation now runs FIRST (before `active_sink` is even bound), so
no orphan emit is possible: any run that emits `ContractStarted`
also emits a terminal event.

### 8. RecursionError on manifest parse (BL-204)

A BL-173 / BL-191 class extension on the manifest-parse leg:
`parse_skill_md` wrapped `yaml.safe_load` in `except yaml.YAMLError`,
missing `RecursionError` (PyYAML recurses through nested mappings
without a depth cap). An adversarial SKILL.md with deeply nested
YAML now raises the documented `SkillManifestError` instead of an
internal exception, preserving the `discover_skill` / `install_skill`
public contract.

### 9. MultiDispatcher NaN weights (BL-205)

A BL-159 class extension: `MultiDispatcher.__init__` validated
length-alignment of `weights` but not finiteness; the downstream
clamp `max(0.0, min(1.0, score))` collapses NaN to confidence 1.0
(the exact BL-159 trap closed for `cosine_similarity` /
`LLMDispatcher` / `SkillBasedDispatcher`). A finite-and-non-negative
guard at the constructor now surfaces the configuration bug at the
API boundary.

### 10. evaluate_trajectory input-validation mislabel (BL-206)

A novel finding: the input-payload validation
(`input_model.model_validate(case.input_payload)`) sat inside the
same `try` as `run_under_contract`, and `ValidationError` was mapped
to ``output_invalid`` in `_EXC_LABEL`. A malformed fixture therefore
scored as a contract output failure, and an author who wrote
`expected="output_invalid"` could silently green-light a case that
never reached the contract. The validation now runs above the try
so a fixture error raises at the fixture layer.

### 11. InstrumentedDispatcher failure telemetry (BL-207)

A BL-189 / BL-167 class extension: `InstrumentedDispatcher.dispatch`
recorded `calls`, latency, and the `DispatchObserved` event only
after the inner dispatch returned. A failing inner (a `DispatchError`
from an LLM-backed inner, `asyncio.CancelledError`, etc.)
propagated the exception with no stats update and no event, so a
workload monitoring `fallback_rate` to detect routing health saw
`0/0` regardless of how many dispatch attempts crashed -- the
opposite of what observability is supposed to surface. A `try /
finally` now records stats and emits the event on every call
(success or failure); the exception is re-raised unchanged.

### 12. Routing-lane meta-skills excluded from routing (BL-208)

A dispatch-precision finding: `dispatcher-skill` (the in-tree
routing meta-skill with `lane: routing`) leaked into
`KeywordDispatcher`'s catalog. `SkillBasedDispatcher` excluded it by
bare name; the keyword / embedding / LLM dispatchers did not. The
golden eval set happens not to contain routing-themed queries, so
the P@1/MRR=1.0 gate passed today; a real user query like "How do
I route this to a skill?" would have returned the meta-skill,
breaching its own SKILL.md contract. A new
`SkillRegistry.routable()` method filters out `lane == "routing"`
skills; every candidate-iterating dispatcher (`KeywordDispatcher`,
`EmbeddingDispatcher`, `LLMDispatcher`) now uses it.
`SkillRegistry.all()` is unchanged, so `SkillBasedDispatcher` (which
needs the routing meta-skill itself for its routing instructions)
still sees the full set.

## Consequences

Every change is additive: an existing call site sees identical
behaviour for every valid input. The strict narrowings (BL-197 NaN
rejection, BL-205 NaN-weight rejection, BL-200 depth cap, BL-208
routing-lane filter) reject inputs that previously silently
mis-behaved; legitimate inputs are unaffected. The diagnostic-gap
fixes (BL-199, BL-201, BL-202, BL-207) add coverage to the failure
path without changing the success path. The audit-ordering fix
(BL-203) reorders the validation, not the success-path behaviour.

Tests added in this increment:

- `tests/memory/test_bl197_bl198_bl199_audit6.py` (17 tests):
  Namespace NaN/inf rejection, `resolve_ttl` semantics, end-to-end
  adapter validation, RedisStore.mset empty short-circuit (skips
  without `fakeredis`), TTLSweeper persistent / intermittent /
  cancellation paths.
- `tests/harness/test_bl200_bl201_bl202_bl203_audit6.py` (14
  tests): Redactor cycle / depth cap / shallow no-op /
  `RedactingSink` survives; `_decode_lines` placeholders for null /
  number / array / undecodable / unchanged for well-formed;
  `emit_wall_clock_exceeded` with / without base / without limit;
  resume-validation no-orphan and well-formed-still-emits.
- `tests/skills/test_bl204_bl205_bl207_bl208_audit6.py` (12
  tests): `parse_skill_md` translates RecursionError, well-formed
  still parses; `MultiDispatcher` rejects bad weights, accepts
  finite non-negative; `InstrumentedDispatcher` records failure
  in stats, emits event on failure; `Registry.routable()`
  excludes routing-lane, `all()` unchanged, `KeywordDispatcher`
  no longer returns routing-lane skills.
- `tests/evaluation/test_bl206_audit6.py` (2 tests): malformed
  input raises ValidationError; well-formed still completes.

`make check` passes; `mypy` (strict, 71 source files) is clean;
`ruff check` / `ruff format --check` clean; pytest coverage 94.96%
(above the 94% gate); `make schema` clean; `scripts/eval.py` PASS;
REUSE 3.x compliant.

### Revisit triggers

- The runbook 7.4 candidates 2 (`_balanced_spans` extractor) and
  3 (`RetryPolicy` partial usage) remain open. Candidate 2 was
  the original "revisit if a fifth bound is added"; this audit
  added no new bound there. Candidate 3 is upstream-dependent
  (PydanticAI partial-usage on the exception path).
- An out-of-tree `IterableKeyProvider` that revokes keys between
  `iter_key_ids` and `key` (BL-196) would surface a `KeyError`
  from the multi-key loop. M3 from the audit triage; deferred as
  defence-in-depth (no in-tree trigger).
- `wrap_encrypted` docstring still does not flag
  `VersionedMemoryStore` / `TransactionalMemoryStore` as
  deliberately not-forwarded (M6 doc gap). Deferred to the next
  consolidation pass; the Protocol-level docs in `store.py`
  already document this.
- `MarkdownValidatorRuntime` per-line comment tracker mis-tracks
  prose `--` sharing a line with `<!--` / `-->`. H5 from the audit
  triage; demo-only (`workloads/_example/`), no production caller.
  Deferred as documented limitation in the example workload's
  README, or as a future targeted fix.
