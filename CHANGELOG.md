# Changelog

Material changes by phase. Format follows Keep a Changelog; dates are
ISO 8601. Pre-1.0, so this is phase-based, not semver-tagged.

## [Unreleased] Approval-context payload on the interruption (ADR 0031, BL-251, 2026-06-13)

The data-carrying half of BL-251 (the held-out follow-on to the BL-242
authority tiers): the blast-radius tier and a proposed rollback path now
travel onto the human-facing approval interruption, so an approver (or a
UI) sees what they are confirming and how it would be undone. Additive to
L1 (ADR 0007); ADR 0031 is the cross-cutting why.

### Added

- `ApprovalInterruption.tier` and `ApprovalInterruption.rollback_plan`
  (both optional, default `None`). ADR 0029 surfaced the tier onto the
  guard's `GuardResponse`; this carries it, and a rollback plan, through
  to the interruption, symmetrically across the replay and deferred
  resume paths.
- `RollbackPlanner` Protocol + `MappingRollbackPlanner` reference in
  `harness/authority.py` (beside `TierClassifier`):
  `plan(tool, arguments) -> str | None`, workload-supplied so the
  framework binds no domain knowledge (ADR 0001). A model-driven planner
  satisfies the same Protocol.
- `GuardResponse.rollback_plan`; `HarnessToolGuard(rollback_planner=...)`
  consulted only on the approval branch (it never changes a decision);
  `run_under_contract(rollback_planner=...)` threading into the default
  guard.
- 15 deterministic tests
  (`tests/harness/test_bl251_approval_context.py`);
  `harness/authority.py`, `harness/guard.py`, and
  `harness/interruption.py` at 100% line coverage.

### Notes

- The deferred path records `(tier, rollback_plan)` keyed by the call's
  `tool_call_id` during the gate (the response is otherwise discarded
  when it raises the framework's `ApprovalRequired`), so the deferred
  pause carries the same approval context the replay path reads straight
  off the `GuardResponse`. No change to the audited resume-verification
  or side-effect semantics.
- A `rollback_planner` only annotates an approval some other rule already
  requires, so unlike `tier_classifier` it does not trigger guard
  construction by itself.
- The behavioural half of BL-251 (an evidence-capture hook around a
  Tier 3 action, the two-step parameter-restatement confirmation on
  resume) is split forward to `BL-252`.

## [Unreleased] DEGRADED disposition and grounding postconditions (ADR 0030, BL-244, 2026-06-13)

The output-trustworthiness item from the Vertex MCP analysis (#114): a
"delivered but degraded" disposition that travels with the run record,
and the deterministic grounding check that is the highest-value
anti-confabulation postcondition for a retrieval agent. Additive to L1
(ADR 0007); ADR 0030 is the cross-cutting why.

### Added

- `RunRecord.degraded` (record schema v1.1.0; `RUN_RECORD_SCHEMA_VERSION`
  advances to `1.1.0` with `1.0.0` retained in
  `SUPPORTED_RUN_RECORD_SCHEMA_VERSIONS`). The orthogonal quality axis
  beside `RunOutcome`: `outcome` stays `completed` (the run did not
  halt), and `degraded` flags that a SOFT postcondition was violated on
  the final delivered leg. The field defaults `False`, so a v1.0.0 and a
  v1.1.0 record both validate against the current model and the offline
  gate needs no per-version dispatch.
- `harness/grounding.py`: `ungrounded_citations(claim, sources, *,
  pattern)`, a pure function returning the citation tokens (regex
  matches of `pattern`) in `claim` absent from `sources`, in
  first-appearance order, deduplicated; and `grounding_predicate(extract,
  *, pattern, name=, severity=SOFT)`, a `Predicate` factory whose
  workload-supplied `extract(state) -> (claim, sources)` keeps the
  framework free of any output-model shape (ADR 0001). The check
  relabels without rewriting model content.
- `run_under_contract` sets `degraded`: a per-leg `leg_soft_failed` flag,
  reset at each leg's start (so a retry that recovers clears it) and
  captured into the terminal `completed` record. A `substitute`
  directive keeps the flag (the replacement is not re-validated);
  `escalate` and a HARD violation stay their own non-`completed`
  terminals at the `False` default.
- 20 deterministic tests
  (`tests/harness/test_bl244_degraded_and_grounding.py`);
  `harness/grounding.py` at 100% line coverage.

### Notes

- `degraded` is a separate axis, not a new `RunOutcome` member, to
  preserve the ADR 0012 lockstep between `RunOutcome` and
  `evaluation.dataset.TrajectoryOutcome` and leave every existing
  outcome-matching call site unchanged. The ok / degraded / error
  *reporting* surface (output banner, scheduler exit codes) stays a
  workload concern; the substrate records the disposition.

## [Unreleased] Graduated authority tiers on the guard (ADR 0029, BL-242, 2026-06-13)

The most on-thesis item from the Vertex MCP analysis (#114): authority
keyed to a proposed action's blast radius, beyond the flat
`approval_required` list. Additive to L1 (ADR 0007); ADR 0029 is the
cross-cutting why.

### Added

- `harness/authority.py`: `AuthorityTier` (an ordered IntEnum: OBSERVE /
  LOW / STATEFUL / IRREVERSIBLE), the `TierClassifier` Protocol
  (`classify(tool, arguments) -> AuthorityTier`, workload-supplied so the
  framework binds no domain knowledge, ADR 0001), and the deterministic
  `MappingTierClassifier` reference (a tool-name-to-tier map with a
  fail-safe STATEFUL default for unlisted tools).
- `HarnessToolGuard(tier_classifier=..., approval_tier=...)`: when a
  classifier is supplied, an action classified at `approval_tier`
  (default STATEFUL) or above is escalated to REQUIRE_APPROVAL beyond the
  static `approval_required` list. `GuardResponse` gains a `tier` field
  annotating APPROVE and REQUIRE_APPROVAL (never set on REJECT).
- `run_under_contract(tier_classifier=..., approval_tier=...)`: threads
  the classifier into the default guard (a classifier alone now triggers
  guard construction).
- 12 deterministic tests
  (`tests/harness/test_bl242_authority_tiers.py`); `harness/authority.py`
  and `harness/guard.py` at 100% line coverage.

### Notes

- The tier-driven escalation lives entirely in the guard (it produces
  more REQUIRE_APPROVAL decisions the existing runtime / resume already
  handles), so the audited approval / resume machinery (ADR 0027) is
  untouched. The Tier 2 / 3 approval context (rollback plan, evidence
  capture, the tier on `ApprovalInterruption`, the two-step parameter
  restate) is split forward to `BL-251`.

## [Unreleased] Hybrid retrieval and decay-ranked demotion (ADR 0028, BL-243 / BL-247, 2026-06-13)

The first implementation wave from the Vertex MCP analysis (#114): two
in-tree memory retrieval-quality gaps closed, both additive to L1
(ADR 0007). ADR 0028 is the cross-cutting why.

### Added

- `memory/retrieval.py`: `fuse_rrf` (deterministic Reciprocal Rank
  Fusion over ranked id lists, the conventional `k=60` damping),
  `lexical_overlap_scores` (a dependency-free token-overlap keyword
  baseline), the `Reranker` Protocol (the cross-encoder analogue of
  `Embedder`, model injected), the `HybridSemanticStore` extension
  Protocol, and the `HybridHit` result type (`BL-243`).
- `InMemorySemanticStore.query_hybrid`: a vector pass fused with a
  lexical pass via RRF, then an optional rerank over a
  recall-then-rerank window (`rrf_k` is validated positive at the API
  boundary). The store now retains indexed source text
  in lockstep with the vector index; vector-only `query_semantic` is
  unchanged.
- `TieredMemoryStore.demote_to_capacity(rank_key=...)`: an optional
  strength-ranking hook; `rank_key=None` keeps the first-write FIFO
  order byte-for-byte. `memory.tiering.decay_strength` is the
  deterministic Ebbinghaus forgetting reference to pass as `rank_key`,
  with finite / non-negative input validation, and a non-finite
  `rank_key` result is rejected at demotion (the BL-159 / BL-231
  non-finite-control class) (`BL-247`).
- 32 deterministic tests
  (`tests/memory/test_bl243_hybrid_retrieval.py`,
  `tests/memory/test_bl247_demotion_ranking.py`); `memory/retrieval.py`
  at 100% line coverage.

### Changed

- `LIMITATIONS.md` L5 narrows: the hybrid fusion and the pluggable
  demotion ranking are now in tree; only the embedder, the optional
  reranker, and a durable vector / keyword backend stay the workload's
  integration. The `BitemporalMemoryStore` half of BL-247 is forwarded
  to `BL-250`.

## [Unreleased] Vertex MCP cross-pollination analysis (2026-06-13)

A deep audit of what the substrate can learn from a long-running,
single-operator MCP gateway, recorded as
`docs/analysis/vertex-mcp-lessons.md` with eight forward-looking
capability proposals tracked as `BL-242` through `BL-249`.
Documentation only; no code change, nothing adopted (each item is a
maintainer decision).

### Added

- `docs/analysis/vertex-mcp-lessons.md`: the analysis, ranked by
  leverage and fit, each lesson mapped to a precise repo surface
  (`harness/guard.py`, `memory/semantic.py`, `harness/provenance.py`,
  `memory/tiering.py`, the `evaluation/` gate) and an additive-to-L1
  proposal. Headlines: graduated authority tiers on the guard
  (`BL-242`), RRF hybrid-retrieval fusion (`BL-243`), and a DEGRADED
  disposition with grounding postconditions (`BL-244`).
- `BL-242`-`BL-249` in `docs/backlog.md` (a new "Vertex MCP
  cross-pollination" section), and a `docs/analysis/` index entry in
  `docs/README.md`.

## [Unreleased] Deferred (non-replay) approval resume (ADR 0027, BL-114, 2026-06-12)

The deepest known approval-flow limitation (`LIMITATIONS.md` L10),
unblocked by pydantic-ai 1.106 (`DeferredToolRequests` /
`DeferredToolResults` stable, verified in-session with an end-to-end
spike). Additive only (ADR 0007); ADR 0027 is the cross-cutting why.

### Added

- `PydanticAIRuntime(approval_mode="replay"|"deferred")`, validated
  at construction; replay (default) is byte-identical L1/L2
  behaviour. In deferred mode the leg ends with the collected
  approvals, the serialized message history travels in the new
  optional `ResumableState.runtime_state` (JSON-able, default
  `None`), and the resumed leg continues from it: prior tool calls
  run exactly once and only the continuation is charged. Approvals
  bind by the run's own tool_call_ids and are re-verified at
  execution by the full (tool, arguments) tuple (BL-193); a
  mismatched, consumed, or tampered approval re-pauses instead of
  executing. MCP tools share the same deferred gate.
- Deliberate, documented divergences when opting in: denial becomes
  a model-visible `ToolDenied` error (the run continues; no terminal
  `ApprovalDenied`), the paused leg's usage is charged at the pause
  boundary, resume requires a decision for every pending approval,
  and `stream()` still gates in replay mode.
- 12 deterministic tests
  (`tests/harness/test_bl114_deferred_resume.py`), headlined by the
  non-replay proof (a pre-pause side-effect tool executes exactly
  once across pause + resume).

### Changed

- The guard REJECT branch is factored into a `_rejection` helper
  shared verbatim by the replay and deferred gates, so hard/soft
  governance is provably identical in both modes (no behaviour
  change; the existing suite passes unmodified).

## [Unreleased] Prompt caching on the runtime adapter (ADR 0026, BL-132 / BL-171, 2026-06-12)

The longest-tracked Tier 1 capability after `BL-120`, unblocked by
pydantic-ai 1.106 (verified in-session: `CachePoint`, the
`AnthropicModelSettings` cache controls,
`RunUsage.cache_read_tokens` / `cache_write_tokens`). Additive only
(ADR 0007); ADR 0026 is the cross-cutting why.

### Added

- `PydanticAIRuntime(model_settings=...)`: forwarded verbatim to the
  underlying Agent (`None` preserves prior behaviour). The opt-in
  surface for provider-side cache breakpoints on the stable
  tools/system prefix; opaque to the harness, vendor-neutral like
  `model` (ADR 0001).
- `BudgetTracker.consume_cache_tokens(read=, write=)` plus readable
  `cache_read_tokens` / `cache_write_tokens` counters: pure
  accounting, no new ceiling, deliberately not charged to
  `max_tokens` (upstream reports cache counts outside
  `input_tokens`) and outside `snapshot()` (BL-154 carries enforced
  dimensions only; the key set is now regression-pinned). Pairs with
  `consume_cost` (BL-123); negative counts rejected (the BL-221
  caller-fed input class).
- `run()` and `stream()` surface the counts getattr-guarded (the
  `_usage` compat stance), streaming at the final reconciliation.
- 16 deterministic tests (`tests/harness/test_bl132_prompt_caching.py`).
  Live cache-hit validation (identical-prefix second run with
  `cache_read_tokens > 0`) is coupled to `BL-120`;
  `LIMITATIONS.md` L9 retitled to the deterministic-only residual.

## [Unreleased] BL-240 secret-scan gate, BL-241 DCO reconciliation (2026-06-12)

The two maintainer decisions ADR 0025 deferred, decided and landed.

### Added

- A blocking `secret-scan` CI job (gitleaks/gitleaks-action v3.0.0,
  SHA-pinned, full-history checkout, PR comments disabled so no write
  permission is needed) in the `ci-success` aggregate (`BL-240`).
  `.gitleaks.toml` extends the default rules and allowlists exactly
  one literal, the synthetic AKIA redaction fixture; verified locally
  (with the config: no leaks over tree + 52 commits; without: the 4
  fixture hits fire).

### Changed

- `CONTRIBUTING.md` (`BL-241`): DCO 1.1 certification is by
  pull-request submission; the per-commit `Signed-off-by` trailer is
  welcome but no longer mandated (squash-merge consolidates trailers
  and no CI check enforced them, so the requirement was unverifiable
  as written).

## [Unreleased] Fourteenth audit: full-pass engagement, process hardening (ADR 0025, BL-236 / BL-237 / BL-238 / BL-239, 2026-06-12)

A fourteenth audit run under an external full-pass engagement
protocol (inventory, validation baseline, security audit, quality
audit, gated remediation, documentation, ADR, backlog, report), with
the phase evidence under `audit/00-inventory.md` through
`audit/03-final-report.md`. No runtime code finding: the recurring
fault classes were re-walked and hold, and the ADR 0024 modules
(`memory/compaction.py`, `memory/tiering.py`) received their first
audit coverage clean. The findings sit in the gates and declarations
around the code. ADR 0025 is the cross-cutting why.

### Changed

- `ci.yml` `dependency-audit`: the stale `--ignore-vuln
  PYSEC-2025-183` suppression removed (`BL-236`): the locked pyjwt is
  2.13.0 and the unsuppressed `pip-audit --strict` run is clean, so
  the suppression's own revisit trigger had fired; a kept ignore
  could only mask a future advisory republished under the same ID.
  The two quoted commands in `docs/runbook.md` are synced.

### Added

- `ci.yml` `dependency-audit`: `uv lock --check` as the first step
  (`BL-237`), failing the PR when `uv.lock` is stale relative to
  `pyproject.toml` (the drift class that occurred on 2026-05-25 and
  was hand-remediated on 2026-05-27; that engagement's recommendation
  is now landed). Verified exit 0 on the current tree and exit 1 on a
  deliberately drifted `pyproject.toml`.
- `audit/` phase evidence for this pass and a root `BACKLOG.md`
  deferred-items register (canonical tracker stays
  `docs/backlog.md`).

### Removed

- The unused direct `logfire>=4.34.0` declaration from `[project]
  dependencies` (`BL-238`). Zero references in source, tests, or
  docs; the resolved graph is unchanged (logfire remains a transitive
  of `pydantic-ai` via `pydantic-ai-slim[...,logfire,...]`), so this
  is declaration hygiene, not an install-surface change. Full gate
  re-run baseline-identical (1170 passed, 95.25 % coverage).

### Fixed

- Documentation accuracy (`BL-239`, docstring-only): the
  `TieredMemoryStore` stamp-map growth caveat (pruned only during
  `demote_to_capacity`) and the `memory/_expiry.py` wording that
  still described the BL-197 TTL boundary validation as pending.
- Post-ADR-0024 drift (findings register D-7): `README.md` status
  paragraph and memory capability bullet, `docs/README.md` ADR
  enumeration (ended at ADR 0022), `docs/runbook.md` next-audit-slot
  markers and section 8 per-document rows.

## [Unreleased] Compaction, summarisation, tiering (ADR 0024, BL-234 / BL-235, 2026-06-09)

The long-horizon context-engineering half of `BL-135` (S2), closing
the item: every prior reclamation mechanism (lazy expiry, the active
sweeper, the size-bound capacity pass) reclaims space by dropping
entries; this wave adds condensing and tiering. Both deliverables are
drivers/compositions over the existing store Protocols (the
`TTLSweeper` / BL-131 precedents): no new store Protocol, no adapter
changes, additive only (ADR 0007). ADR 0024 is the cross-cutting why.

### Added

- `memory.compaction` (`BL-234`): the `Summarizer` Protocol
  (bytes-in/bytes-out, memory-local so the framework binds no vendor,
  ADR 0001); `TruncatingSummarizer`, the deterministic head-plus-tail
  byte-budget reference (exact `max_bytes` output on over-budget
  input, load-time `ValueError` when the marker leaves no content
  budget); `MemoryCompactor`, a driver folding N source entries into
  one summary entry. Atomic mode (default) requires
  `VersionedMemoryStore` + `TransactionalMemoryStore` at construction
  (load-time `TypeError`) and commits the summary write plus all
  source deletes in one version-gated `transact`: a concurrent
  rewrite/expiry/delete of any source fails the whole transaction and
  `compact` returns `None` (no lost update, no partial application).
  `atomic=False` is the explicit opt-in for transaction-less backends
  (S3): write-summary-then-delete-sources, crash-safe to re-compact,
  with the documented single-writer lost-update window. Rolling
  compaction (target among the sources) is supported. Returns a frozen
  `CompactionResult` (source keys, bytes before/after, the summary's
  version token in atomic mode).
- `memory.tiering.TieredMemoryStore` (`BL-235`): a hot/cold two-tier
  composition behind the plain `MemoryStore` surface, namespace
  `name`/`workload` agreement checked at construction
  (`retention_seconds` may differ per tier). Reads fall through hot to
  cold and promote (CAS-guarded when the hot tier implements
  `CASMemoryStore`, so a raced hot write is never clobbered); writes
  land hot-first then invalidate cold (opt-out per write); deletes go
  cold-first so a fall-through read can never resurrect deleted data;
  `demote(keys)` is version-gated on a `VersionedMemoryStore` hot tier
  (a raced rewrite stays hot, not counted); `demote_to_capacity` ranks
  by the wrapper's first-write sequence with the BL-224/BL-225 legacy
  sentinel (unknown keys oldest, ties lexicographic), demoting
  overflow to cold instead of dropping it. Inner-tier extension
  Protocols are deliberately not forwarded (ADR 0004 "don't fake it").
- Exports: `Summarizer`, `TruncatingSummarizer`, `MemoryCompactor`,
  `CompactionResult`, `TieredMemoryStore` from `memory`.

### Changed

- `LIMITATIONS.md` L5 narrows: compaction/summarisation/tiering are
  now in-tree; the remaining gaps (durable `SemanticMemoryStore`
  adapter, LRU ranking, model-quality summarizer/embedder) are
  deliberate out-of-tree extension points. `BL-135` is fully resolved
  in `docs/backlog.md`.

### Tests

- 66 new test cases (`tests/memory/test_bl234_compaction.py`, 30;
  `tests/memory/test_bl235_tiering.py`, 36): Protocol satisfaction,
  truncation arithmetic (including the `joined[-0:]` zero-tail
  guard), atomic commit/conflict/rolling/TTL/audit paths on
  `InMemoryStore` and `SQLiteStore`, best-effort paths and their
  construction-time rejections, promotion/CAS races, version-gated
  demotion under a raced rewrite, capacity ranking with legacy and
  promoted keys, per-tier TTL behaviour, and the review-hardening
  boundaries: wrapper-level key validation, the failed-CAS promotion
  not stamping, the cold-invalidation failure not stripping a landed
  write's stamp, the lost demote race leaving no stale cold ghost
  (rewrite and delete variants), the capacity prune keeping a
  concurrent write's stamp, and the best-effort per-source
  delete-failure containment.

## [Unreleased] Thirteenth code audit (ADR 0023, BL-233, 2026-06-06)

The thirteenth in-depth code audit, by area, against the same green
gates (ruff, ruff format, mypy strict, pytest at `cov-fail-under=94`,
schema-drift, REUSE 3.x, `pip-audit`, the dispatch evaluation gate). It
re-walked the fan-out per-member failure containment class (`BL-222`
`MultiDispatcher`, `BL-223` `MultiSink`, `BL-227`
`BoundedS3Store.evict_to_capacity`, `BL-228` `RoutingChainDispatcher`)
against the one sibling surface no prior audit had reached: the
periodic TTL sweep. ADR 0023 is the cross-cutting why. One finding,
spanning the two network adapters with the per-item-loop shape.

### Fixed

- `memory.s3.S3Store._sweep_sync` and
  `memory.dynamodb.DynamoDBStore._sweep_sync` (`BL-233`) now contain a
  per-item network DELETE failure (`try/except Exception: continue`,
  counting only successful deletes), mirroring
  `BoundedS3Store.evict_to_capacity._delete_all` (`BL-227`). Before, the
  per-item `delete_object` / `delete_item` was bare, so a single
  transient backend error (S3 `SlowDown` / throttle, DynamoDB
  `ProvisionedThroughputExceeded`, a network blip) on one expired item
  propagated out of the loop and aborted the entire sweep pass: every
  later expired item in the same listing / scan was left un-swept for
  the cycle, and the count of items already deleted in this pass was
  discarded. The `TTLSweeper` loop survived (`BL-199`) but each retry
  re-LISTed / re-HEADed the whole keyspace, so a steady low rate of
  transient errors could keep a large keyspace's tail permanently
  un-swept. This is the `BL-227` containment class unreached on the
  sibling sweep path, and the precise question ADR 0020 / 0021 / 0022
  deferred from the `BL-229` `_head_metadata` scope.

### Changed

- The inspection step of each sweep stays fail-loud by design (the S3
  HEAD via `_head_metadata`, the DynamoDB `Scan`): an object the
  sweeper cannot *inspect* (a real AccessDenied / NoSuchBucket, not a
  not-found) still surfaces as an error, so the sweep never silently
  skips a keyspace it has lost read access to. Only the idempotent
  DELETE *action* is best-effort. `BaseException` (`KeyboardInterrupt`,
  `SystemExit`, `asyncio.CancelledError`) still propagates per the
  `BL-165` / `BL-223` invariant. `DynamoDBStore`'s eviction stays
  all-or-nothing via `_batch_write`-with-retry (a bounded cap-meeting
  operation differs from unbounded periodic sweep), so S3 is internally
  consistent (per-item best-effort sweep + evict) while DynamoDB's
  sweep is best-effort and its evict batched, each the right shape for
  the operation. The happy path (no DELETE error) is byte-identical.

### Tests

- 8 new regression tests
  (`tests/memory/test_bl233_sweep_delete_containment.py`), 4 per
  adapter via the BL-226 / BL-227 `moto` + flaky-client pattern: a
  partial failure sweeps the rest and returns the success count without
  raising (the failed item stays alive); every DELETE failing returns 0
  without raising (all items retried next cycle); the happy path is
  unchanged; a `SystemExit` on DELETE still propagates. Verified to fail
  against the pre-fix code (the four containment cases raise the
  injected `ClientError`).

## [Unreleased] Twelfth code audit (ADR 0022, BL-231 / BL-232, 2026-06-01)

The twelfth in-depth code audit, by area, against the same green
gates (ruff, ruff format, mypy strict, pytest at `cov-fail-under=94`,
schema-drift, REUSE 3.x, `pip-audit`, the dispatch evaluation gate).
It re-walked the non-finite-numeric class (`NaN` / `+inf` subverts a
numeric control because every ordered comparison with `NaN` is False
and `+inf <= 0` is also False) against the numeric *configuration*
boundaries the prior NaN audits (BL-159 cosine, BL-205 weights,
BL-221 consume-cost, BL-226 S3 metadata, all on value / data
boundaries) had not reached, the peers of the
`Namespace.retention_seconds` boundary BL-197 hardened. ADR 0022 is
the cross-cutting why. Two findings, split by sub-mechanism.

### Changed

- `harness.budgets.ActionBudget` (`BL-231`) gains a
  `model_validator(mode="after")` that rejects a `NaN` / `+inf` / `-inf` /
  negative value on every numeric limit (`max_cost_usd`,
  `max_wall_clock_seconds`, the per-tool wall-clock map, and the
  integer count limits / maps) at construction. Before, a
  `NaN` / `+inf` limit constructed cleanly and the tracker's
  `consumed > limit` check was always False, so the ceiling the
  operator declared was silently disabled for the whole run. This is
  the dual of `BL-221`, which hardened the *consumed* side of the same
  comparison but left the *limit* side open. `None` (unlimited) and
  `0` (a zero ceiling, e.g. `max_cost_usd=0.0`) stay valid, so every
  existing budget is unaffected. The validator is a runtime check, not
  a JSON-Schema constraint, so the generated schema is unchanged by it.
- `harness.runtime.RetryPolicy` (`BL-231`) gains a `__post_init__` that
  rejects negative `max_retries`, `NaN` / `+inf` / `-inf` / negative
  `backoff_base_seconds` / `backoff_max_seconds`, and a
  `circuit_breaker_threshold` that is not `None` or `>= 1`. A `NaN`
  backoff made `delay_for` non-finite, and `asyncio.sleep(NaN)` returns
  immediately, turning the bounded exponential backoff of `BL-136` into
  a no-delay retry storm against the failing provider.
- `harness.mcp.MCPServerSpec` (`BL-232`) timeout validator gains a
  `math.isfinite` conjunct: `timeout_seconds <= 0` alone let `NaN` and
  `+inf` through (both comparisons are False), passing a guard whose
  docstring claims "must be positive". The message becomes "a positive,
  finite number"; the docstring change propagates to the generated
  `workload-manifest.json` schema `description` (the only schema diff
  in this wave).
- `memory.sweep.TTLSweeper` (`BL-232`) interval validator gains the
  same `math.isfinite` conjunct. A `NaN` interval slipped the `<= 0`
  guard and drove `asyncio.wait_for(self._stop.wait(), timeout=NaN)`,
  which raises `TimeoutError` immediately, turning the maintenance loop
  into a no-delay busy-sweep that hammers the backend's `sweep_expired`
  (Redis / DynamoDB / S3 network I/O) as fast as the event loop allows.
  `max_keys` is unchanged (an `int | None` with no `NaN`
  representation; its `<= 0` guard already covers the meaningless
  integers).

### Added

- 39 new regression tests:
  `tests/harness/test_bl231_bl232_numeric_config.py` (33: `ActionBudget`
  rejects `NaN` / `+inf` / `-inf` / negative on every limit and per-tool map
  value and still accepts `None` / `0` / finite-positive, with a pinned
  demonstration that a `NaN` cost limit would have disabled the ceiling
  and a finite ceiling still fires; `RetryPolicy` rejects bad backoff /
  `max_retries` / `circuit_breaker_threshold` and still accepts the
  documented defaults; `MCPServerSpec` rejects `NaN` / `+inf` and still
  rejects `0` / negative and accepts a positive finite timeout) and
  `tests/memory/test_bl232_sweeper_interval.py` (6: `TTLSweeper` rejects
  `NaN` / `+inf` / `-inf` and still rejects `0` / negative and accepts a positive
  finite interval).
- ADR 0022 (`docs/adr/0022-twelfth-code-audit.md`): the twelfth-audit
  narrative, the value-vs-configuration boundary generalisation, the
  deliberate scope boundary on bare-float control parameters, and the
  event-model-output non-finding.

### Documentation

- `docs/adr/README.md`: ADR 0022 row added.
- `docs/backlog.md`: `BL-231` / `BL-232` added (resolved) under a new
  "Twelfth code audit (ADR 0022, 2026-06-01)" section; source `S10`
  (Python comparison semantics + `math.isfinite`) added; the header
  date line extended.
- `STATUS.md` / `LIMITATIONS.md` / `README.md` / `docs/README.md` /
  `docs/runbook.md` / `SECURITY.md`: post-ADR-0022 sweep per the runbook
  §8 procedure. The phase table gains the twelfth-audit row; the
  ADR-enumeration markers advance to `0022`; the runbook §2.3
  fault-class table gains the BL-231 / BL-232 numeric-configuration
  row; living-doc "Last reviewed" dates advance to 2026-06-01.
- `CLAUDE.md`: both ADR-enumeration paragraphs extended through ADR
  0022.

## [Unreleased] Eleventh code audit (ADR 0021, BL-228 / BL-229, 2026-05-31)

The eleventh in-depth code audit, by area, against the same green
gates (ruff, ruff format, mypy strict, pytest at `cov-fail-under=94`,
schema-drift, REUSE 3.x, `pip-audit`, the dispatch evaluation gate).
It closed the two open ADR 0020 revisit triggers and re-walked the
recurring fault classes (fan-out containment, audit-vs-raise parity,
LIST-then-HEAD concurrency, NaN / unparseable input) against any
surface a prior audit fixed only pointwise. ADR 0021 is the
cross-cutting why. Two findings (`skills/dispatchers/chain.py`,
`memory/s3.py`) plus one documented non-finding (`memory/dynamodb.py`).

### Changed

- `skills.dispatchers.chain.RoutingChainDispatcher.dispatch` (`BL-228`)
  contains a per-link failure: a link that raises `Exception` (a
  network `LLMDispatcher` raising `DispatchError` or timing out, an
  embedding provider blip) is treated as "produced no usable match" and
  the chain falls through to the next link, preserving the best-effort
  matches already gathered from cheaper links. `BaseException`
  (`KeyboardInterrupt`, `SystemExit`, `asyncio.CancelledError`) still
  propagates (the BL-165 / BL-222 / BL-223 terminal-signal invariant).
  This is the BL-222 / BL-223 / BL-227 fan-out containment class on the
  sequential cheap-first chain and the resolution of the deferred ADR
  0019 / ADR 0020 revisit trigger. `default_dispatcher` (BL-103)
  composes a `RoutingChainDispatcher`, so an LLM-tier failure on the
  recommended default path now degrades to the keyword / embedding tier
  instead of surfacing as a whole-dispatch crash. The happy path and
  the empty-list-return path are unchanged.
- `memory.s3.S3Store._sweep_sync` and
  `memory.s3.BoundedS3Store._collect_live_sync` (`BL-229`) route their
  per-object HEAD through the new `S3Store._head_metadata` helper, which
  returns `None` when the object is not found. A concurrently-deleted
  object (the LIST-then-HEAD window, where HeadObject returns HTTP
  `404 NoSuchKey`) is now skipped instead of crashing the whole
  `sweep_expired` / `evict_to_capacity` scan. The `_collect_live_sync`
  half was new (BL-227 contained only `evict_to_capacity`'s per-key
  DELETE loop, not the collect-phase HEAD); the `_sweep_sync` half is
  the narrow resolution of the ADR 0020 revisit trigger. A
  non-not-found `ClientError` (throttle, `AccessDenied`, outage) still
  propagates, matching `_get_live`'s "do not misreport an outage as an
  absent key" stance. S3 DeleteObject is idempotent, so only the HEAD
  needs the guard.

### Added

- `memory.s3.S3Store._head_metadata(s3_key: str) -> dict[str, str] | None`:
  HEADs an object and returns its user metadata, or `None` when not
  found (typed `NoSuchKey`, or a `ClientError` whose code is
  `NoSuchKey` / `404` / `NotFound`), mirroring the `_get_live`
  not-found idiom; any other `ClientError` propagates.
- 16 new regression tests:
  `tests/skills/test_bl228_chain_member_failure.py` (7: failing middle
  link falls through to a later success, failing last link preserves
  the earlier best-effort match, failing first link does not abort, all
  links fail returns empty, a high-confidence cheap winner
  short-circuits before the failing link, `BaseException` parametrized
  over `KeyboardInterrupt` / `SystemExit` / `CancelledError`
  propagates, happy path unchanged) and
  `tests/memory/test_bl229_s3_head_toctou.py` (9: `_head_metadata`
  returns metadata / `None` on 404 / `None` on typed `NoSuchKey` /
  propagates a non-404 error; `sweep_expired` skips a
  concurrently-deleted object, propagates a non-404 HEAD error, happy
  path unchanged; `evict_to_capacity` skips a concurrently-deleted
  object, propagates a non-404 HEAD error).

### Not changed (documented non-finding)

- `memory.dynamodb` `float(exp)` (`BL-230`): the four bare-`float`
  metadata reads (`_live_item`, `_list_sync`, `_scan_sync`, the
  `compare_and_set` match branch) are the same shape BL-226 fixed in
  S3, but the DynamoDB `N` type is server-validated to a documented
  finite range (positive `1E-130` to `~9.9E+125`; "exceeding this
  results in an exception"), so NaN / +inf / -inf / unparseable cannot
  be stored through its API, and `item.get("exp", {}).get("N")` already
  reads a wrong-type (String) attribute as no-TTL. The BL-226 class is
  inapplicable because the boundary's own type system forbids the
  offending values, unlike S3's free-form user-metadata. Recorded so a
  future audit does not re-flag it; no code change.

## [Unreleased] Tenth code audit (ADR 0020, BL-226 / BL-227, 2026-05-26)

The tenth in-depth code audit, by area, against the same green gates
(ruff, ruff format, mypy strict, pytest at `cov-fail-under=94`,
schema-drift, REUSE 3.x, `pip-audit`, the dispatch evaluation gate).
The clear bugs were fixed additively in the same increment; ADR 0020
is the cross-cutting why. This audit re-walked the same *classes* the
prior audits fixed pointwise, with particular attention to the just-
merged `BL-225` `BoundedS3Store` and whether the same
"untrusted-input must not crash" / "audit-vs-raise parity" /
"per-item failure containment" invariants applied to its new
boundaries. Two new findings in `memory/s3.py`.

### Added

- `memory.s3._safe_float(v: str | None) -> float | None`: parses a
  float from untrusted S3 user metadata, returning `None` on missing
  / unparseable / non-finite (NaN / +inf / -inf via `math.isfinite`).
  The non-finite rejection closes the BL-159 / BL-205 / BL-221
  NaN-bypass class on the metadata-read trust boundary: a corrupted
  `x-amz-meta-expires-at = "nan"` would otherwise sail through
  `float()` and then through `now > NaN` (always `False`),
  permanently masking the object from lazy / sweep / capacity
  expiry.
- `memory.s3._safe_int(v: str | None) -> int`: parses an int from
  untrusted S3 user metadata, returning `0` on missing /
  unparseable. Zero matches the BL-225 legacy-migration default (an
  object without `insertion-order` is treated as the oldest and
  evicts first), so a corrupted value falls back to the most
  defensive eviction-order semantic.
- 33 new regression tests
  (`tests/memory/test_bl226_bl227_audit10.py`): 13 parametrized
  `_safe_float` cases (None, empty, unparseable, NaN with mixed
  case, +inf / -inf / Infinity, plus valid floats including 0,
  negative, large exponents); 9 parametrized `_safe_int` cases
  (None, empty, unparseable, float-string-not-an-int, NaN, inf,
  plus valid ints); 3 parent-`S3Store` cases (corrupt `expires-at`
  does not crash `read` / `sweep_expired`; NaN `expires-at` does
  not silently mask); 3 `BoundedS3Store` cases (corrupt
  `insertion-order` / `expires-at` do not crash
  `evict_to_capacity`; NaN `insertion-order` is treated as legacy
  seq=0); 4 BL-227 cases (partial-failure audits only successes;
  all-fail returns 0 and emits no audit; happy path unchanged;
  `SystemExit` still propagates).

### Changed

- `memory/s3.py`: every metadata-read call site (`S3Store._get_live`,
  `S3Store._sweep_sync`, `BoundedS3Store._collect_live_sync`) now
  routes through `_safe_float` / `_safe_int`. Before: a corrupted or
  hand-written `expires-at` / `insertion-order` would raise
  `ValueError` past the documented exception contract, crashing the
  whole keyspace's read / sweep / eviction scan on the first bad
  entry. After: a corrupted entry parses to None (no TTL) / 0
  (legacy default), and the scan completes normally. NaN /
  +inf / -inf in `expires-at` no longer silently masks the object
  from expiry; the non-finite value is rejected at the parse
  boundary instead of leaking into `is_expired` where the IEEE 754
  `now > NaN = False` invariant would mask the entry forever.
- `BoundedS3Store.evict_to_capacity` (BL-227): the per-key
  `delete_object` loop inside `asyncio.to_thread` now contains
  per-key exceptions, collects the actually-deleted keys, and emits
  audit only for them. Before: a single failing DELETE (S3
  throttle, transient access drift, network blip) propagated out of
  the thread and the audit-emit loop below was never reached,
  leaving partial state mutation with no audit at all, breaking the BL-202
  / BL-167 audit-vs-raise parity invariant. After: per-key
  containment of `Exception` (parity with BL-222 `MultiDispatcher`
  and BL-223 `MultiSink`); `BaseException` (`KeyboardInterrupt`,
  `SystemExit`, `asyncio.CancelledError`) still propagates for the
  BL-165 / BL-223 terminal-signal invariant. The function returns
  the count of *actual* deletions, not *attempted* ones, so
  `TTLSweeper.evicted_total` is truthful. A failed key stays alive
  and the next `TTLSweeper` cycle retries it, matching the BL-199
  sweeper-resilience contract already applied to the age-only sweep
  path.

### Documentation

- `docs/adr/0020-tenth-code-audit.md`: cross-cutting reasoning for
  the audit, the class-of-fault generalisations, the open
  revisit-triggers.
- `docs/adr/README.md`: ADR 0020 added to the index.
- `docs/backlog.md`: `BL-226` / `BL-227` added (resolved) under a new
  "Tenth code audit (ADR 0020, 2026-05-26)" section.
- `STATUS.md` / `LIMITATIONS.md` / `README.md` / `docs/README.md` /
  `docs/runbook.md`: post-ADR-0020 sweep per the runbook §8
  procedure. The "today" / ADR-enumeration / capability markers
  advance to ADR 0020 + `BL-226` / `BL-227`; the README status
  paragraph and the runbook §8.1 README row pick up
  `BoundedDynamoDBStore` and `BoundedS3Store` alongside the prior
  `BoundedRedisStore`; the runbook §2.3 fan-out per-member-failure
  fault-class row extends to include `BL-227`
  (`BoundedS3Store.evict_to_capacity` sequential DELETE); the
  runbook §2.3 open-backlog row for `BL-135` is updated to reflect
  that the size-bound half is now fully delivered across every
  in-tree adapter via `BL-212`-`BL-214` / `BL-224` / `BL-225`.
  Living-doc "Last reviewed" dates advanced to 2026-05-27.
- `STATUS.md` L3 Tier 0 row, `LIMITATIONS.md` L4, `SECURITY.md`
  "Supply chain" bullet, `docs/releasing.md` tracking line, and the
  `docs/runbook.md` §4.1 "ready" set: drop the now-resolved
  `BL-150` reference. Every workflow `uses:` line in
  `.github/workflows/ci.yml`, `codeql.yml`, and `release.yml` is
  commit-SHA pinned with the version in a trailing comment
  (`BL-150` resolved 2026-05-25, PR #66); the supply-chain
  remainder reduces to `BL-151` (signed publish-to-index). The
  pinning itself is not new in this sweep; only the surface
  documentation now catches up.

## [Unreleased] BL-225: BoundedS3Store (BL-135 size-bound on S3, 2026-05-26)

The cold-storage S3 extension to BL-214's Redis reference, BL-213's
SQLite reference, and BL-224's DynamoDB reference, closing the S3 half
of `BL-135` (the size-bound half) so every in-tree adapter now
supports the size cap. S3 has no native insertion-order column or
server-side atomic counter primitive, so the adapter ships as an opt-in
subclass that stamps a per-object `insertion-order` user-metadata
attribute on every PUT, set to `time.time_ns()` at write time. No
auxiliary index is needed: every data object carries its own ordering
attribute directly. The bare `S3Store` is unchanged for every existing
caller. The single S3-specific divergence is the wall-clock ordering
source: S3 has no equivalent atomic counter primitive (conditional
writes are recent and not universally available), so multi-writer
deployments with clock skew can reorder writes across writers,
consistent with the S3Store eventual-consistency contract.

### Added

- `memory.BoundedS3Store`: opt-in subclass of `S3Store` that overrides
  `write` to stamp an `insertion-order` user-metadata attribute on
  every `PutObject`, set to `time.time_ns()` at write time. The
  metadata stamp coexists with the existing `expires-at` TTL stamp;
  S3's 2 KiB user-metadata cap is well above both. The bare `S3Store`
  ignores the new attribute, so existing readers / sweepers continue
  to work byte-for-byte the same. A rewrite of an existing key gets
  a fresh `insertion-order`, so the rewritten key orders as *newest*,
  matching the BL-213 SQLite `INSERT OR REPLACE` semantic, the
  BL-214 Redis ZADD-rescore semantic, and the BL-224 DynamoDB
  seq-replacement semantic and diverging from the BL-212 InMemoryStore
  first-write FIFO.
- `BoundedS3Store.evict_to_capacity`: LISTs the namespace prefix,
  HEADs each object to read `insertion-order` and `expires-at`,
  filters expired-but-unswept items client-side (the BL-195
  read-vs-listing parity in S3 form so a dead object does not
  double-evict a live one), sorts the live entries by
  `(insertion-order, key)` ascending (the secondary key sort is the
  deterministic tie-break for the migration case where multiple
  legacy items share `insertion-order = 0` and for the rare
  same-nanosecond collision on a fast host), and DELETEs the oldest
  `(live_count - max_keys)` objects in one parallelised thread call.
  One audit `MemoryDelete` event per evicted key (BL-040). The cost
  shape (LIST + HEAD-per-object + DELETE-per-evicted) mirrors the
  parent's `sweep_expired`, so eviction is no more expensive than
  the existing age-only sweep.
- New regression suite `tests/memory/test_bl225_s3_bounded_sweeper.py`
  (23 tests, moto-backed): Protocol satisfaction (subclass yes, bare
  `S3Store` no but still `SweepableStore`); oldest-first eviction by
  insertion-order ascending; the overwrite-shifts-to-newest contract;
  no-op at and under the cap; non-positive cap rejection
  (parametrized); expired-but-unswept items excluded from the live
  count; per-key audit emission; TTLSweeper integration on both age
  and capacity passes; `mset` preserves dict insertion order under
  FIFO (loop-of-write through the overridden `write` stamps a fresh
  insertion-order per item in dict iteration order); `write_content`
  carries the insertion-order (delegates through `write`); the
  migration contract (a legacy object written via a bare `S3Store`
  has no `insertion-order` attribute, is treated as
  `insertion-order = 0` in eviction sort and evicts first, but
  rewriting it via the bounded subclass stamps a fresh value and
  moves it to newest); namespace prefix isolation (one namespace's
  eviction does not touch another); parent surface unchanged
  (read / write / delete / TTL / list_keys roundtrip); and the
  `wrap_acl` / `wrap_encrypted` forwarding (BL-156, the existing
  `_ACLBoundedMixin` and `_EncBoundedMixin` infrastructure picks up
  the new subclass automatically through `isinstance(...,
  BoundedSweepableStore)`).

### Changed

- `memory.__init__` exports `BoundedS3Store`.
- `memory/s3.py` module docstring now notes the opt-in subclass and
  the per-object `insertion-order` metadata design.
- `LIMITATIONS.md` L5 updated to note the S3 delivery alongside
  InMemoryStore, SQLiteStore, Redis, and DynamoDB. The size-bound
  half of `BL-135` is now closed across every in-tree adapter; the
  long-horizon compaction / summarisation / tiering half stays
  tracked under `BL-135`.
- `memory/README.md` capability bullet now enumerates the five
  eviction orderings (SQLite by rowid, Redis by index score,
  DynamoDB by `seq` attribute via server-side counter, S3 by
  per-object `insertion-order` user-metadata attribute, InMemoryStore
  by dict insertion order).

### Documentation

- `docs/backlog.md`: `BL-225` added (resolved) under the existing
  "Sweeper size bound (2026-05-23)" section; `BL-135`'s
  delivered / remaining narrative updated to reflect the fifth and
  final adapter shipped (closing the size-bound half of `BL-135`).

## [Unreleased] BL-224: BoundedDynamoDBStore (BL-135 size-bound on DynamoDB, 2026-05-24)

The network-durable DynamoDB extension to BL-214's Redis reference and
BL-213's SQLite reference, parallel to how BL-180 extended BL-124 from
the in-tree reference to the network-durable adapters. DynamoDB has no
native insertion-order column either, so the adapter ships as an
opt-in subclass that stamps a per-namespace monotonic `seq` Number
attribute on every data item, allocated via an atomic
`UpdateItem ADD seq :n` on a per-namespace counter row. The bare
`DynamoDBStore` is unchanged for every existing caller. Closes the
DynamoDB half of `BL-135` (the size-bound half); `S3Store` stays
tracked under `BL-135` as the remaining durable backend.

### Added

- `memory.BoundedDynamoDBStore`: opt-in subclass of `DynamoDBStore`
  that stamps a per-namespace monotonic `seq` Number attribute on
  every data item and maintains a per-namespace counter row at
  `pk = "__evict_counter::<namespace>"` (placed outside the
  `<namespace>::*` data prefix, so it cannot collide with a user
  data item: the namespace-name validator's `^[a-z0-9]` rule makes
  a colliding namespace structurally impossible, and the counter row
  does not appear in `list_keys` / `scan` / `sweep_expired` results,
  all of which filter by `begins_with(pk, "<namespace>::")`). Seq allocation
  uses DynamoDB's atomic `UpdateItem ADD seq :one` action (creates
  the counter with `seq = 1` on first use; increments server-side
  thereafter), so ordering is strict insertion-order FIFO across
  concurrent writers without clock skew. Every keyspace-mutating
  method (`write`, `mset`, `compare_and_set`, `write_versioned`,
  `transact`) is overridden to allocate a seq before the PutItem
  and stamp it onto the item. A rewrite of an existing key
  allocates a fresh seq and the PutItem replaces the whole item,
  so the rewritten key orders as *newest* by seq, matching the
  BL-213 SQLite `INSERT OR REPLACE` semantic and the BL-214 Redis
  ZADD-rescore semantic and diverging from the BL-212 InMemoryStore
  first-write FIFO.
- `BoundedDynamoDBStore.evict_to_capacity`: scans the namespace's
  data items (the counter row is outside the namespace prefix so
  the FilterExpression naturally excludes it), projects only
  `pk` / `seq` / `exp` (kept small via `ProjectionExpression`),
  client-side filters expired-but-unswept items (the BL-195
  read-vs-listing parity in DynamoDB form so a dead row does not
  double-evict a live one), sorts the live entries by `(seq, key)`
  ascending (secondary key sort is a deterministic tie-break for
  the migration case where multiple legacy items share `seq = 0`),
  and batch-deletes the oldest `(live_count - max_keys)` items via
  the parent's `_batch_write` (which retries throttled items and
  raises on retry-budget exhaustion, so audit emission only fires
  on full success). One audit `MemoryDelete` event per evicted
  key (BL-040).
- New regression suite `tests/memory/test_bl224_dynamodb_bounded_sweeper.py`
  (29 tests, moto-backed): Protocol satisfaction (subclass yes,
  bare `DynamoDBStore` no but still `SweepableStore`); oldest-first
  eviction by seq ascending; the overwrite-shifts-to-newest
  contract; no-op at and under the cap; non-positive cap rejection
  (parametrized); the counter-row isolation suite (no leak into
  `list_keys` / `scan` / `sweep_expired`; no collision with a
  user-written key named `__evict_counter`); expired-but-unswept
  items excluded from the live count; per-key audit emission;
  TTLSweeper integration on both age and capacity passes; the
  seq-consistency suite (every parent mutation path stamps a fresh
  seq across `write`, `mset` + `mdelete`, `compare_and_set`,
  `compare_and_delete`, `write_versioned` + `delete_versioned`,
  `transact`); monotonic counter source (counter advances exactly
  once per write, exactly N times per `mset`/`transact` batch in
  one `UpdateItem ADD`); strict FIFO across a tight loop;
  dict-insertion-order preservation under `mset` / `transact`; the
  migration contract (a legacy item written via a bare
  `DynamoDBStore` has no `seq` attribute, is treated as `seq = 0`
  in eviction sort and evicts first, but rewriting it via the
  bounded subclass stamps a fresh seq and moves it to newest);
  empty-batch short-circuit on `mset` (no counter UpdateItem, no
  batch_write, parity with the BL-198 RedisStore and BL-178
  SQLiteStore fixes).

### Changed

- `memory.__init__` exports `BoundedDynamoDBStore`.
- `memory/dynamodb.py` module docstring now notes the opt-in
  subclass and the per-namespace `seq` attribute design.
- `LIMITATIONS.md` L5 updated to note the DynamoDB delivery
  alongside InMemoryStore, SQLiteStore, and Redis; the remaining
  `S3Store` stays tracked under `BL-135` because it needs an
  auxiliary index over LIST + DELETE.
- `memory/README.md` capability bullet now enumerates the four
  eviction orderings (SQLite by rowid, Redis by index score,
  DynamoDB by `seq` attribute via server-side counter, InMemoryStore
  by dict insertion order).

### Documentation

- `docs/backlog.md`: `BL-224` added (resolved) under the existing
  "Sweeper size bound (2026-05-23)" section; `BL-135`'s
  delivered / remaining narrative updated to reflect the fourth
  adapter shipped.

## [Unreleased] Ninth code audit (ADR 0019, BL-223, 2026-05-24)

The ninth in-depth code audit, by area, against the same green gates
(ruff, ruff format, mypy strict, pytest at `cov-fail-under=94`,
schema-drift, REUSE 3.x, `pip-audit`, the dispatch evaluation gate).
The clear bug was fixed additively in the same increment; ADR 0019 is
the cross-cutting why. This audit re-walked the same *classes* the
prior audits fixed pointwise, with particular attention to the BL-222
"per-member failure containment" guarantee from ADR 0018 and whether
the class generalises to other fan-out paths in the tree. One finding,
a class extension on the audit-sink fan-out side.

### Fixed

- `BL-223`: `harness.sinks.MultiSink.emit` now contains per-sink
  `Exception` failures so a single failing sink (a flaky OTel
  exporter, a disk-full `JsonlSink`, any sink with a transient
  network or filesystem error) does not prevent downstream sinks
  from receiving the event. Without the containment, the
  enforcement loop's `active_sink.emit(BudgetExceededEvent(...))`
  or `active_sink.emit(GovernanceViolated(...))` could be lost on
  the OTLP sink because the in-process JsonlSink failed first, or
  vice versa: a bare `raise BudgetExceeded(...)` then arrived in
  the caller without a matching event in the downstream-of-the-
  failure sinks, breaking the BL-202 / BL-167 audit-vs-raise
  parity invariant ("every state-affecting raise has a matching
  audit event") at the fan-out boundary. The fix wraps each
  `sink.emit(event)` in a per-sink `try / except Exception` and
  continues; `BaseException` (`KeyboardInterrupt`, `SystemExit`,
  `asyncio.CancelledError`) still propagates so terminal signals
  are not swallowed (parity with the runtime's BL-165 "do not
  reinterpret cancellation as a pause" invariant). BL-222 class
  extension on the audit fan-out side.

### Tests

- 7 new regression tests
  (`tests/harness/test_bl223_multi_sink_failure.py`): failing
  middle sink does not block downstream sinks, failing first sink
  does not block subsequent sinks, all-failing returns cleanly,
  `KeyboardInterrupt` propagates, happy-path fan-out unchanged,
  empty fan-out is a no-op, multi-event sequence with one
  intermittent failing sink delivers every healthy event in order.
- Coverage at 94.94% (above the 94% gate; the absolute number
  fluctuates with pytest discovery and the new tests, the gate is
  what matters).

## [Unreleased] Eighth code audit (ADR 0018, BL-219-BL-222, 2026-05-24)

The eighth in-depth code audit, by area, against the same green
gates (ruff, ruff format, mypy strict, pytest at `cov-fail-under=94`,
schema-drift, REUSE 3.x, `pip-audit`, the dispatch evaluation gate).
The clear bugs were fixed additively in the same increment; ADR 0018
is the cross-cutting why. This audit re-walked the same *classes*
the prior audits fixed pointwise and the code paths exercised by the
BL-212-BL-214 sweeper-size-bound wave plus the ADR 0016 (`BL-133`)
IPC surface. Four findings, all class extensions of bugs the prior
audits fixed elsewhere.

### Fixed

- `BL-219`: `harness.sinks.JsonlSink.emit` now pins `encoding="utf-8"`
  on its `Path.open("a", ...)` call. Without the explicit encoding,
  a non-UTF-8 platform locale (Windows cp1252, C locale ASCII) would
  either raise `UnicodeEncodeError` past the sink boundary or
  silently mis-encode a non-ASCII event payload (a localised error
  message, a unicode prompt template, a redacted span carrying high
  bytes), corrupting the audit stream. BL-218 class extension on the
  write side: the read-side standard (`Path.read_text(
  encoding="utf-8")` everywhere) now applies to the write side too.
- `BL-220`: `skills._executor_child._read_frame` now treats a
  1 / 2 / 3-byte partial header as EOF (the parent crashed mid-write
  after sending part of the 4-byte length prefix), mirroring the
  empty-header branch and the parent's reciprocal handling at the
  documented `SkillContractExecutorError` boundary in
  `skills.execution._read_frame`. Without the check,
  `_FRAME_LEN.unpack(header)` raised `struct.error` past the child's
  clean EOF path, crashing the child with an unhandled exception
  instead of exiting through the main loop's EOF branch. BL-216
  class extension on the child side.
- `BL-221`: `harness.budgets.BudgetTracker.consume_cost(usd)` and
  `consume_tool_call(..., wall_clock_seconds=)` now validate
  `math.isfinite(...)` and non-negativity at the entry boundary,
  raising `ValueError` with a diagnostic naming the argument.
  Without the validation, a single NaN cost report or wall-clock
  attribution (a buggy pricing helper, a misconfigured adapter that
  emits NaN on a zero-token request) silently disabled the budget
  ceiling for the rest of the run: NaN is truthy in Python (so the
  `if usd:` short-circuit did not skip it), NaN propagates through
  `+` (so the accumulator becomes NaN for the rest of the run), and
  `NaN > limit` is always `False` (so the `_check` strict-greater
  comparison never trips). BL-159 / BL-205 class extension on the
  budget input boundary.
- `BL-222`: `skills.dispatchers.multi.MultiDispatcher.dispatch` now
  uses `asyncio.gather(*, return_exceptions=True)` and skips
  exceptional results in the aggregation loop. Without the change,
  a single flaky member (an LLM-backed inner that raised
  `DispatchError`, an embedding provider that timed out) cancelled
  every sibling task and crashed the entire ensemble. The cancelled
  siblings' `InstrumentedDispatcher` `try/finally` wrappers (BL-207)
  then emitted `fell_back=True / matched=0` events, polluting the
  routing-health telemetry with cancellation-as-fallback noise. An
  `Exception` member now contributes 0 to the AVERAGE / WEIGHTED /
  VOTE blend, parity with the documented "a member that did not
  return the skill contributes 0" semantic; the exception is
  contained at the ensemble boundary. BL-207 / BL-208 class
  extension on the ensemble side.

### Tests

- 20 new regression tests:
  `tests/harness/test_bl219_bl221_audit8.py` (11 tests on the JsonlSink
  UTF-8 encoding and the BudgetTracker finite/non-negative validation),
  `tests/skills/test_bl220_executor_child_partial_header.py` (5 tests
  on the child-side partial-header EOF treatment), and
  `tests/skills/test_bl222_multi_member_failure.py` (4 tests on the
  MultiDispatcher member-failure containment).
- Coverage at 94.98% (above the 94% gate, up from 94.97%).

## [Unreleased] Seventh code audit (ADR 0017, BL-215-BL-218, 2026-05-23)

The seventh in-depth code audit, by area, against the same green
gates (ruff, ruff format, mypy strict, pytest at `cov-fail-under=94`,
schema-drift, REUSE 3.x, `pip-audit`, the dispatch evaluation gate).
The clear bugs were fixed additively in the same increment; ADR 0017
is the cross-cutting why. This audit targeted the *classes* the
prior audits fixed pointwise and the new IPC surface introduced by
ADR 0016 (`BL-133`). Four findings, all in `skills/` or the
`read_text` encoding boundary.

### Fixed

- `BL-215`: `skills.loader.parse_skill_md` and the lazy
  `_read_body_only` (used by `Skill.body()`) now catch
  `UnicodeDecodeError` and re-raise as `SkillLoadError`. A SKILL.md
  that is not valid UTF-8 (latin-1, a binary file misnamed, a
  UTF-16 BOM at the head) previously leaked a Python-internal
  exception past the documented `SkillLoadError` boundary; the fix
  translates to the documented "unreadable file" branch.
  BL-204 class extension.
- `BL-216`: `skills.execution._read_frame` (parent side, reading
  from the subprocess) and `skills._executor_child._read_frame`
  (child side, reading from the parent) now cap the body length at
  64 MiB (`_FRAME_MAX_BODY_BYTES`). The 4-byte big-endian length
  prefix can encode up to ~4 GiB; without the cap, a compromised
  child writing `2**32 - 1` would drive the parent into a multi-GiB
  allocation before discovering the truncation. The parent raises
  `SkillContractExecutorError` and kills the subprocess; the child
  treats an oversize header as EOF (defence in depth). New class
  introduced by ADR 0016; the "external-input-must-not-crash"
  invariant from BL-167 / BL-200 / BL-201 generalised to the new
  IPC boundary.
- `BL-217`: `skills.execution.SubprocessSkillContractExecutor.load`'s
  `_proxies` closure now validates every metadata item from the
  child structurally before constructing `_PredicateProxy`: non-dict
  raises, missing `name` / `severity` raises, non-string types
  raise, unknown severity raises, all with a diagnostic naming the
  slot and the failure mode. Without the check, a malformed item
  (a buggy or malicious child) leaked `KeyError` / `ValueError`
  past the documented `SkillContractExecutorError` boundary.
  BL-159 / BL-205 class extension applied to the new IPC metadata
  frame.

### Changed

- `BL-218`: `workloads.loader._build_loaded_workload`,
  `evaluation.dataset.load_dispatch_golden`, and
  `workloads/_example/__main__.py` now specify
  `encoding="utf-8"` on their `Path.read_text` calls, restoring
  consistency with the project's
  explicit-UTF-8 convention (the `check_run_records.py` /
  `gen_schema.py` / `skills.loader` precedents). A non-default
  platform locale (Windows cp1252, a C locale ASCII) would have
  silently mis-decoded non-ASCII content in any of the three
  affected reads.

### Added

- 15 new regression tests across three new test modules:
  `tests/skills/test_bl215_loader_unicode.py` (4 tests),
  `tests/skills/test_bl216_subprocess_frame_bound.py` (6 tests),
  `tests/skills/test_bl217_subprocess_metadata_validation.py`
  (5 tests). Total test count: 925 (up from 910).
- ADR 0017: the seventh-audit narrative and the consequences for
  the IPC trust boundary.

### Documentation

- `docs/backlog.md`: new "Seventh code audit (ADR 0017,
  2026-05-23)" section with `BL-215` through `BL-218` resolved.
- `docs/adr/README.md`: ADR 0017 row added.
- `STATUS.md`, `README.md`, `CLAUDE.md`, `LIMITATIONS.md`:
  phase tracking, ADR enumeration, and document-maturity refreshed
  for the seventh audit.
- `docs/runbook.md`: section 8.1 off-by-one fix ("six" -> "seven"
  files), seventh-audit slot recorded, last-reviewed bumped.

## [Unreleased] BL-214: BoundedRedisStore (BL-135 size-bound on Redis, 2026-05-23)

The network-durable Redis extension to BL-213's SQLite reference,
parallel to how BL-180 extended BL-124 from the in-tree reference to
the network-durable adapters. Redis has no native insertion-order
column, so the adapter ships as an opt-in subclass that maintains a
per-namespace insertion-order sorted-set index alongside the data
writes. The bare `RedisStore` is unchanged for every existing caller.

PR #60 review: the score source changed from client-side
``time.time()`` to a per-namespace server-side INCR counter before
merge (Copilot + Codex P1/P2 on clock skew + sub-microsecond
tie-breaks). The change closes three review findings in one design
swap: multi-writer deployments stay correct under clock skew, a
tight write loop on a single writer gets unique monotonic scores,
and a batched ``mset`` / ``transact`` no longer collapses every
member onto a shared score that Redis tie-breaks lexicographically.
The cost is one extra Redis round trip per write or per batch (the
INCR / INCRBY). The auxiliary counter key is
``__evict_counter::<namespace>``, placed under the same outside-the-
namespace-prefix isolation convention as the index key.

### Added

- `memory.BoundedRedisStore`: opt-in subclass of `RedisStore` that
  maintains a single per-namespace sorted-set index at
  `"__evict_index::<namespace>"` and a server-side INCR counter at
  `"__evict_counter::<namespace>"` (both placed outside the
  `<namespace>::*` keyspace prefix, so neither can collide with a
  user-written key and neither appears in `list_keys` / `scan`
  results: the namespace-name validator's `^[a-z0-9]` rule makes a
  colliding namespace structurally impossible) and implements
  `SweepableStore` plus `BoundedSweepableStore`. Index scores come
  from the server-side counter (INCR for a single write, INCRBY for
  a batch), so ordering is strict insertion-order FIFO even across
  clock skew, sub-microsecond ties, or batched writes; eviction is
  ZRANGE ascending by score. A re-write of an existing key allocates
  a new score so a rewritten key orders as *newest* by index,
  matching the BL-213 SQLite overwrite-shifts-to-newest semantic and
  diverging from the BL-212 InMemoryStore first-write FIFO. Every
  keyspace-mutating method on the parent (`write`, `mset`, `delete`,
  `mdelete`, `compare_and_set`, `compare_and_delete`,
  `write_versioned`, `delete_versioned`, `transact`) is overridden
  to call `super()` then update the index via INCR / INCRBY + ZADD
  (or ZREM for deletes), so the index stays consistent across every
  mutation path.
- `BoundedRedisStore.sweep_expired`: cleans stale index members
  whose underlying Redis data keys have already been auto-evicted
  by Redis itself (catch-up bookkeeping; the data key is gone via
  Redis's own expiry, only the auxiliary entry remains).
- `BoundedRedisStore.evict_to_capacity`: walks the index
  oldest-first via ZRANGE, performs the same staleness filter as
  `sweep_expired` so an expired-but-unswept member does not count
  toward the cap (the BL-195 read-vs-listing parity in Redis form),
  pipelines DEL on the live oldest block plus a final ZREM on the
  index, and emits one audit `MemoryDelete` per evicted key.
- New regression suite `tests/memory/test_bl214_redis_bounded_sweeper.py`
  (22 tests, fakeredis-backed): Protocol satisfaction (subclass yes,
  bare `RedisStore` no); oldest-first eviction by index score; the
  overwrite-shifts-to-newest contract; no-op at and under the cap;
  non-positive cap rejection (parametrized); the index-isolation pair
  (no leak into `list_keys` / `scan`; no collision with a user-written
  key named `__evict_index`); `sweep_expired` stale-cleanup; zero on
  a clean index; expired-but-unswept members excluded from the live
  count; per-key audit emission; sweeper integration on both age and
  capacity passes against a Redis store; the index-consistency suite
  (every parent mutation path keeps the index aligned across `write`,
  `mset` + `mdelete`, `compare_and_set`, `compare_and_delete`,
  `write_versioned` + `delete_versioned`, and `transact`).

### Changed

- `memory.__init__` exports `BoundedRedisStore`.
- `memory/redis.py` module docstring now notes the opt-in subclass
  and the per-namespace insertion-order sorted-set index.
- `LIMITATIONS.md` L5 updated to note the Redis delivery alongside
  InMemoryStore and SQLiteStore; the remaining `DynamoDBStore` and
  `S3Store` stay tracked under `BL-135` because neither has a native
  insertion-order column.
- `memory/README.md` capability bullet now enumerates the three
  eviction orderings (SQLite by rowid, Redis by index score,
  InMemoryStore by dict insertion order).

### Documentation

- `docs/backlog.md`: `BL-214` added (resolved) under the existing
  "Sweeper size bound (2026-05-23)" section; `BL-135`'s
  delivered / remaining narrative updated to reflect the third
  adapter shipped.

## [Unreleased] BL-213: BoundedSweepableStore on SQLiteStore (2026-05-23)

The natural durable-single-host counterpart to `BL-212`'s
`InMemoryStore` reference (parallel to the `BL-124` SQLite reference
for the version-token Protocol). Adds the second adapter behind the
new Protocol; the remaining durable backends stay tracked under
`BL-135` and each needs an auxiliary index because none has a native
insertion-order column the way SQLite's rowid does.

### Added

- `SQLiteStore.evict_to_capacity(max_keys: int) -> int`: runs the
  count + select-oldest + delete inside one `BEGIN IMMEDIATE`
  transaction (parity with the BL-161 mset/mdelete transactional
  shape) so a concurrent writer cannot interleave between the
  live-count read and the delete. ``now`` is sampled after the
  `BEGIN IMMEDIATE` lock is held (Codex PR #59 P2), so a contended
  write lock that blocks for up to the sqlite3 default 5-second
  timeout cannot strand a stale timestamp that lets just-expired
  keys still count as live. Oldest-first is by SQLite rowid;
  ``INSERT OR REPLACE`` is implemented as delete-then-insert, and
  the inserted row's rowid is strictly greater than every other
  rowid currently in the table, so an overwrite orders as newest
  by rowid (this is the ordering property; SQLite does not
  guarantee monotonic / never-reused rowids without
  ``AUTOINCREMENT``, per the Copilot wording fix). The SQL
  counterpart of the BL-195 `is_live` predicate
  (`expires_at IS NULL OR expires_at >= :now`) filters
  expired-but-unswept rows out of the live count and the
  eviction candidate set. The DELETE chunks the rowid IN list
  (chunk size 500, conservative under
  `SQLITE_LIMIT_VARIABLE_NUMBER`'s pre-3.32 default of 999) so a
  large overflow does not raise `OperationalError: too many SQL
  variables` (Codex PR #59 P1); all chunks share the same
  transaction so atomicity holds across them. Audit emission per
  evicted key (BL-040).
- New regression suite `tests/memory/test_bl213_sqlite_bounded_sweeper.py`
  (13 tests): Protocol satisfaction; oldest-first eviction by rowid;
  the overwrite-orders-as-newest contract (the SQLite divergence
  from InMemoryStore's first-write FIFO, pinned by test); no-op at
  and under the cap; non-positive cap rejection; expired-but-unswept
  rows excluded from the live count; per-key audit emission; sweeper
  integration on both age and capacity passes against a durable
  SQLite store; the chunked-DELETE path (monkeypatching the chunk
  constant to a small value and verifying eviction crosses multiple
  chunk boundaries without raising).

### Changed

- `memory/sqlite.py` module docstring now lists `BoundedSweepable`
  among the implemented extension Protocols, with the rowid-based
  ordering and the `INSERT OR REPLACE` overwrite-shifts-to-newest
  semantic divergence from `InMemoryStore` called out explicitly.
- `LIMITATIONS.md` L5 notes the SQLite delivery alongside
  InMemoryStore; the remaining durable adapters (`RedisStore`,
  `DynamoDBStore`, `S3Store`) stay tracked because none has a native
  insertion-order column.
- `memory/README.md` capability bullet now lists `InMemoryStore` and
  `SQLiteStore` as the two backends behind `BoundedSweepableStore`,
  with a note on the SQLite-specific ordering semantic.

### Documentation

- `docs/backlog.md`: `BL-213` added (resolved) under the existing
  "Sweeper size bound (2026-05-23)" section; `BL-135`'s
  delivered/remaining narrative updated to reflect the second
  adapter shipped.

## [Unreleased] BL-212: sweeper size bound (BL-135 size-bound half, 2026-05-23)

The size-bound half of `BL-135` lands as a separate ID (`BL-212`) so
the long-horizon compaction / summarisation / tiering half stays
tracked under `BL-135`. The new Protocol is a strict superset of
`SweepableStore`; the default `TTLSweeper` behaviour is unchanged for
every existing caller.

### Added

- `memory.BoundedSweepableStore` extension Protocol: a
  `SweepableStore` that also supports `evict_to_capacity(max_keys) -> int`,
  removing the oldest entries (insertion order, Python dict
  semantics: in-place overwrite keeps the key's original position)
  until the live keyspace is at most `max_keys`. The reference
  implementation lands on `InMemoryStore`; durable adapters are
  tracked under `BL-135` (the BL-124 -> BL-180 Protocol-plus-reference
  cadence).
- `TTLSweeper(max_keys: int | None = None)` kwarg: when set, the
  sweeper runs `evict_to_capacity` after the age-only `sweep_expired`
  on each interval. The store must implement
  `BoundedSweepableStore`; a non-bounded store with `max_keys` set
  raises `TypeError` at construction (ADR 0007 load-time validation),
  and a non-positive `max_keys` raises `ValueError`.
- `TTLSweeper.evicted_total` counter, distinct from `swept_total`,
  so an operator can attribute reclamation to write-rate (capacity)
  versus TTL (age).
- `_ACLBoundedMixin` (in `memory.acl`) and `_EncBoundedMixin` (in
  `memory.encryption`) forward `evict_to_capacity` through `wrap_acl`
  and `wrap_encrypted`. The size-bound is content-agnostic, so unlike
  the CAS / Versioned / Transactional Protocols (which depend on the
  ciphertext token's stability) `wrap_encrypted` *does* forward
  `BoundedSweepableStore`. Each mixin is mutually exclusive with the
  plain sweep mixin in the wrap factories so `isinstance` stays
  truthful on a sweep-only inner.
- New regression suite `tests/memory/test_bl212_bounded_sweeper.py`
  (19 tests): Protocol satisfaction; oldest-first eviction; the
  overwrite-keeps-position contract; no-op at and under the cap;
  non-positive cap rejection; expired-entries-do-not-count (so a
  TTL'd entry does not double-evict a live one); audit-event
  emission per evicted key; sweeper integration on both age and
  capacity passes; load-time configuration errors (non-bounded store
  with `max_keys`, non-positive `max_keys`); `max_keys=None`
  byte-for-byte parity with the BL-080 / BL-199 path; `wrap_acl`
  and `wrap_encrypted` forwarding (including the truthful-isinstance
  shape on a sweep-only inner).

### Changed

- `memory.__init__` exports `BoundedSweepableStore`.
- `memory.acl.wrap_acl` chooses `_ACLBoundedMixin` over
  `_ACLSweepMixin` when the inner satisfies the bounded Protocol;
  the two are mutually exclusive at composition.
- `memory.encryption.wrap_encrypted` chooses `_EncBoundedMixin` over
  `_EncSweepMixin` when the inner satisfies the bounded Protocol;
  the docstring now lists `BoundedSweepable` as a forwarded
  Protocol (the GCM-nonce-conflict reasoning that excludes CAS /
  Versioned / Transactional does not apply to a content-agnostic
  count-based eviction).
- `LIMITATIONS.md` L5 is updated to note the size-bound delivery and
  the remaining compaction / summarisation / tiering / durable-
  adapter scope.

### Documentation

- `docs/backlog.md`: `BL-212` added (resolved) under a new
  "Sweeper size bound (2026-05-23)" section; `BL-135` moves from
  `[pending]` to `[in-progress]` with the delivered slice and the
  remaining scope (compaction / summarisation / tiering + durable
  adapters) noted (the `BL-150` partial-close template).
- `memory/README.md` capability bullet now mentions
  `BoundedSweepableStore` and the `max_keys` kwarg on `TTLSweeper`.

## [Unreleased] BL-133: skill contract execution isolation (ADR 0016, 2026-05-23)

The long-standing L3 "the gate is defence in depth, not a sandbox"
limitation now has an opt-in in-tree second tier. The default
behaviour is unchanged for every existing caller.

### Added

- `skills.execution` module with the `SkillContractExecutor` Protocol
  (`BL-133`, ADR 0016): how a skill's `contract.py` is loaded and
  evaluated. Two in-tree references:
  - `InProcessSkillContractExecutor` (default): the L1 behaviour
    preserved exactly (import in this interpreter; predicates
    evaluate here).
  - `SubprocessSkillContractExecutor`: load and evaluate in a
    long-lived Python subprocess with `resource.setrlimit` caps on
    CPU time, address space, and open files (POSIX). Crash isolation
    is real; resource exhaustion is bounded; predicate exceptions
    surface as `SkillContractExecutorError` without killing the
    harness. IPC framing: 4-byte length prefix + body; parent->child
    pickled (parent owns the source), child->parent JSON (so a
    malicious bundle cannot RCE the parent).
- `skills._executor_child` module: the subprocess entry point. Reads
  limits + contract path from the environment, applies
  `setrlimit`, imports the contract, ships metadata, then services
  predicate-evaluation requests.
- `SkillContractExecutorError`: distinguishes isolation-layer
  failures (subprocess crashed, IPC framing broke) from
  `SkillManifestError` (the documented "this contract is
  malformed").
- New regression suite `tests/skills/test_bl133_execution_isolation.py`
  (12 tests): Protocol satisfaction; in-process load + evaluate;
  subprocess load + evaluate with IPC round-trip; missing-export /
  malformed-import / predicate-raise / child-crash boundaries;
  loader-level forwarding; default backward-compatibility.
- ADR 0016 (`docs/adr/0016-skill-execution-isolation.md`): the design
  decision and the trust framework.

### Changed

- `Skill` gains an opt-in `_executor` field. `Skill.contract()` uses
  it when set; defaults preserve the legacy in-process call path
  (additive to L1, ADR 0007).
- `discover_skill(executor=None)` and `install_skill(executor=None)`
  forward the executor to the constructed Skill.
- `LIMITATIONS.md` L3 is rewritten: the gate is no longer "no
  isolation" but "default in-process, opt-in subprocess + rlimit,
  out-of-tree container for capability isolation".
- `docs/schema/skill-manifest.json` is unchanged; no manifest-level
  surface changed.

### Documentation

- `docs/runbook.md`: post-ADR-0016 sweep. The "ready" set in section
  4.1 drops the now-resolved `BL-133` row; the open-backlog listing
  in 4.2 mirrors that change. Section 1's "most recent ADRs" hint
  bumps to (`0014`, `0015`, `0016`); section 2.5's audit-ADR
  template recommendation bumps to ADR 0015 (the latest audit
  template); the audit-wave-cadence enumeration on line 31 bumps to
  `0009-0015`. The Phase G sweep checks in section 8 now cite today's
  state: the README check is `0016` + `BL-133`, the CLAUDE check is
  `0007`-`0016`, the STATUS check is `0001-0016`, the SECURITY
  check cites the BL-133 skill execution isolation hardening, and
  the ADR-immutability row covers `0001`-`0016`.
- `SECURITY.md` "Skill contracts" bullet: the in-tree opt-in second
  isolation tier is now named explicitly. The bullet calls out
  `InProcessSkillContractExecutor` (default, backward-compatible),
  the `SubprocessSkillContractExecutor` `resource.setrlimit` caps
  (CPU, address space, open files on POSIX), and the
  length-prefixed parent->child pickle / child->parent JSON IPC
  framing that prevents a malicious bundle from RCEing the parent
  (`BL-133`, ADR 0016). Capability isolation (container / seccomp)
  is restated as the out-of-tree extension point, parallel to the
  CLAUDE.md wording.

## [Unreleased] ADR 0015 deferred close (BL-209-BL-211, 2026-05-23)

The three items ADR 0015 flagged as deferred (M3 / M6 / H5 in the
audit triage), closed as additive follow-ups. No new ADR; folded
into the ADR 0015 record by reference.

### Fixed

- `EncryptedStore` BL-196 multi-key loop catches `KeyError` alongside
  `InvalidTag` (`BL-209`). Defence-in-depth for an out-of-tree
  `IterableKeyProvider` (KMS-backed) that returns a key id from
  `iter_key_ids` which the underlying provider can no longer
  resolve (key revoked between iteration and lookup). The in-tree
  `RotatingKeyProvider` does not remove keys; this is the
  extension point for third-party providers.
- `MarkdownValidatorRuntime` per-line comment tracker (`BL-211`).
  New module-level helper `_double_dash_outside_comment` walks each
  line position-aware; a line that opens or closes an HTML comment
  and carries prose ``--`` is now flagged correctly. Demo workload;
  no production caller, but the validator is the canonical
  contract-binding example.

### Documentation

- `wrap_encrypted` docstring (`BL-210`) extended to flag all three
  content-hash-token Protocols (`CASMemoryStore`,
  `VersionedMemoryStore`, `TransactionalMemoryStore`) as
  intentionally not-forwarded, with the GCM-nonce reason. The
  Protocol-level docs in `store.py` already documented this; the
  factory-level dual now agrees so an operator wrapping a
  capability-rich backend sees at the composition site why the
  decorated store no longer satisfies the version-token Protocols.
- `docs/backlog.md` updated with `BL-209`-`BL-211` (ADR 0015
  deferred-close section).
- `docs/schema/workload-manifest.json` regenerated; no functional
  drift, just the docstring propagation from `wrap_encrypted`.

## [Unreleased] Sixth code audit (ADR 0015, BL-197-BL-208, 2026-05-23)

Twelve additive findings spread across `memory/`, `harness/`,
`skills/`, and `evaluation/`. Each is a class extension of a prior
audit fix (BL-159 / BL-167 / BL-178 / BL-189 / BL-191 / BL-193 /
BL-195) or a novel diagnostic-gap finding. Default behaviour is
unchanged for every valid input; the strict narrowings reject
inputs that previously silently mis-behaved.

### Added

- `Namespace.resolve_ttl(ttl_seconds)` (`BL-197`): one method, one
  validation (finite + positive) for both the namespace default and
  per-call TTL. Each adapter's `_ttl` / `_effective_ttl` now delegates
  here; the five-way duplication identified as M5 in the audit triage
  is closed.
- `BudgetTracker.emit_wall_clock_exceeded(elapsed)` (`BL-202`): the
  runtime's boundary-fallback path emits a `BudgetExceededEvent`
  before the bare raise so every wall-clock terminal raise pairs
  with the audit stream.
- `Redactor.max_depth: int = 64` field (`BL-200`): recursion cap on
  the audit-redaction walker so a cyclic or pathologically deep
  payload cannot crash the audit path.
- `SkillRegistry.routable()` (`BL-208`): filters out
  `lane == "routing"` meta-skills; every dispatcher that iterates
  the registry now uses it.
- `TTLSweeper.failures_total` / `last_error` (`BL-199`): operator-
  visible counters for the sweeper's failure resilience.
- ADR 0015 (`docs/adr/0015-sixth-code-audit.md`): the sixth-audit
  cross-cutting decisions.
- New regression test suites: `tests/memory/test_bl197_bl198_bl199_audit6.py`,
  `tests/harness/test_bl200_bl201_bl202_bl203_audit6.py`,
  `tests/skills/test_bl204_bl205_bl207_bl208_audit6.py`,
  `tests/evaluation/test_bl206_audit6.py` (45 new tests total).

### Fixed

- `Namespace` rejects non-finite `retention_seconds` (NaN / +inf)
  and (via `resolve_ttl`) non-finite per-call `ttl_seconds`
  (`BL-197`, Copilot BL-195 follow-up).
- `RedisStore.mset` short-circuits on an empty batch
  (`BL-198`, BL-178 class extension; parity with
  `RedisStore.mdelete` and `SQLiteStore.mset`).
- `TTLSweeper._run` catches transient `sweep_expired` exceptions
  instead of letting them silently kill the loop (`BL-199`,
  BL-189 class extension).
- `Redactor._scrub` enforces a depth cap so a cyclic or
  pathologically deep payload returns the placeholder instead of
  crashing the emit chain (`BL-200`, audit-path-must-not-crash
  invariant extended).
- `harness.openai_api._decode_lines` yields a placeholder dict for a
  malformed JSONL row (non-dict or undecodable) so iteration
  continues to completion (`BL-201`, BL-189 class extension).
- The runtime emits `BudgetExceededEvent` on the wall-clock
  boundary-fallback path before the bare raise, so every terminal
  raise has a matching audit event (`BL-202`, BL-189 / BL-167
  class extension).
- `run_under_contract` validates the resume state's pending
  approvals BEFORE emitting `ContractStarted`, so an unresolved
  resume cannot leave an orphan event in the audit stream
  (`BL-203`, BL-167 class extension).
- `parse_skill_md` translates PyYAML's `RecursionError` into the
  documented `SkillManifestError` (`BL-204`, BL-173 / BL-191 class
  extension on the manifest-parse leg).
- `MultiDispatcher.__init__` rejects NaN / inf / negative weights
  at the API boundary (`BL-205`, BL-159 NaN-clamp class
  extension).
- `evaluate_trajectory` runs the input-payload validation outside
  the contract try/except, so a fixture error raises as a
  fixture-layer `ValidationError` instead of being mislabelled
  ``output_invalid`` (`BL-206`).
- `InstrumentedDispatcher.dispatch` uses `try/finally` so a
  failing inner dispatch still records stats and emits
  `DispatchObserved` (`BL-207`, BL-189 / BL-167 class extension).
- `KeywordDispatcher` / `EmbeddingDispatcher` / `LLMDispatcher`
  exclude routing-lane meta-skills from their candidate pool, so
  the `dispatcher-skill` (and any operator-installed routing
  meta-skill) cannot be returned as a task recommendation
  (`BL-208`).

### Changed

- Each memory adapter's `_ttl` / `_effective_ttl` helper is now a
  thin delegate to `Namespace.resolve_ttl`. Same call sites; the
  validation now happens at the namespace boundary instead of
  passing through to `expires_at = NaN`.
- `Namespace.retention_seconds` docstring now documents the
  finite-positive contract; the regenerated
  `docs/schema/workload-manifest.json` reflects the new wording.
- `wrap_encrypted` is unchanged; the `BL-196` opt-in multi-key
  fallback is unaffected by this wave.

### Documentation

- ADR 0015 added; `docs/adr/README.md` index extended.
- `STATUS.md` phase tracking row added; `Last reviewed` date
  bumped to 2026-05-23.
- `docs/backlog.md`: new section "Sixth code audit (ADR 0015,
  2026-05-23)" with `BL-197`-`BL-208`.
- `docs/runbook.md`: `Last reviewed` date updated to reflect the
  ADR 0015 audit pass.

## [Unreleased] BL-196: opt-in multi-key legacy fallback on EncryptedStore (2026-05-23)

Runbook 7.4 candidate 4 (the EncryptedStore legacy migration class).
The `BL-181` authenticated legacy fallback was current-key only
(`LIMITATIONS.md` L16): adopting a `VersionedKeyProvider` on a store
sealed by a plain `KeyProvider` could only read values whose
plaintext key matched the current ring version, and a key the
provider had rotated past could not decrypt legacy data without an
out-of-band re-encryption pass through the old store. BL-196 adds
an opt-in lift over a new optional `IterableKeyProvider` Protocol.
Additive: default behaviour is unchanged; every existing call site
is byte-identical.

### Added

- `memory.encryption.IterableKeyProvider` Protocol with
  `iter_key_ids(namespace) -> Iterable[str]`. Optional capability on
  top of `VersionedKeyProvider`; out-of-tree KMS-backed providers
  decide whether to enumerate (they may not want to pay per call).
  `runtime_checkable` so `EncryptedStore` can detect it at
  construction time.
- `RotatingKeyProvider.iter_key_ids` (in-tree reference). Returns
  the key ring in insertion order (seed first, then each `rotate`
  chronologically).
- New `legacy_multi_key: bool = False` kwarg on
  `EncryptedStore.__init__` and `wrap_encrypted`. When `True`, the
  legacy `_unseal` fallback iterates every historical key in the
  ring after the current-key attempt fails. AES-GCM authentication
  still gates each attempt (false-tag probability `2**-128` per key,
  accumulated `N * 2**-128` across the ring), so the multi-key
  fallback never returns a wrong plaintext.
- `tests/memory/test_bl196_multi_key_legacy.py` covers the
  iteration order, the construction guards (both `VersionedKeyProvider`
  *and* `IterableKeyProvider` required for the opt-in), the BL-181
  preservation when off, the historical-key decrypt when on, the
  AES-GCM "no silent wrong value" guarantee under the multi-key
  path, the envelope-still-preferred case, the malformed-value fast
  path, and the `wrap_encrypted` flag forwarding (11 tests).

### Changed

- `EncryptedStore._unseal` legacy fallback path is restructured so
  the current-key attempt is tried first (preserving BL-181), then,
  when `legacy_multi_key=True`, the iteration of `iter_key_ids`
  begins (skipping the current id already tried). The malformed
  fast-path (`len(sealed) < _NONCE_BYTES`) moves above the decrypt
  attempt so a truly-too-short value short-circuits to the original
  envelope error without consuming a ring iteration.

### Documentation

- `memory/encryption.py` module docstring: new "Multi-key legacy
  fallback (BL-196, opt-in)" paragraph.
- `memory/README.md`: extends the key-provider bullet with the
  `legacy_multi_key` opt-in and the IterableKeyProvider Protocol.
- `LIMITATIONS.md` L16: renamed to "current-key only by default" and
  documents the opt-in lift, the AES-GCM bound on false matches, and
  the KMS-provider rationale for keeping the default off.
- `docs/runbook.md` 7.4 candidate 4: marked resolved (referenced
  `BL-196`); the "open question" about AES-GCM tag strength is
  answered affirmatively.
- `docs/backlog.md`: new section "EncryptedStore multi-key legacy
  migration (2026-05-23)" with the `BL-196` line.

## [Unreleased] BL-195: consolidate the expiry-boundary predicate across adapters (2026-05-23)

Runbook 7.4 candidate 1 (the expiry-boundary class). Five pointwise
fixes (`BL-157` / `BL-168` / `BL-177` / `BL-188` / the BL-180
DynamoDB conditions) were the same invariant in different encodings;
they now share one helper. Additive; observable behaviour is
unchanged for every input.

### Added

- `memory/_expiry.py` with `is_live(now, expires_at) -> bool` and
  `is_expired(now, expires_at) -> bool` (`BL-195`). The module
  docstring binds the Python predicate to its SQL counterpart
  (`expires_at < :now` for the expired half) and DynamoDB DSL
  counterpart (`attribute_not_exists(exp) OR exp >= :now` for the
  live half) as documented equivalents, so the SQL / DSL forms stay
  literal where they execute server-side but the invariant has one
  source. Boundary is inclusive at the instant `now == expires_at`.
- `tests/memory/test_bl195_expiry_predicate.py` pins the boundary
  table, asserts `is_live` / `is_expired` are total negations on
  every (now, expires_at) pair, and adds an end-to-end regression on
  `InMemoryStore` that exercises the inclusive boundary instant
  (the BL-188 prior-fix shape, hardened against a future drift back
  to a strict `>`).

### Changed

- `memory.InMemoryStore`, `memory.SQLiteStore`, `memory.S3Store`,
  `memory.DynamoDBStore`: every Python-side liveness check
  (`_live_value` / `_live_item` / `_get_live`, `list_keys`, `scan`,
  `sweep_expired`) now routes through `memory._expiry`. The
  `SQLiteStore.sweep_expired` SQL form keeps its literal
  `expires_at IS NOT NULL AND expires_at < ?` (server-side
  predicate); the docstring is updated to name the helper as the
  binding equivalent. The DynamoDB `_scan_sync` filter is restructured
  from an inline-negated generator into an explicit `is_live` check
  for readability. No observable behaviour change for any input;
  every existing test passes unchanged.

### Documentation

- `memory/README.md`: new bullet under "Documented deviations and
  decorator scope" naming the helper and the inclusive-at-instant
  boundary.
- `docs/runbook.md` 7.4 candidate 1: marked resolved (referenced
  `BL-195`).
- `docs/backlog.md`: new section "Expiry-boundary consolidation
  (2026-05-23)" with the `BL-195` line.

## [Unreleased] BL-180: VersionedMemoryStore on durable adapters + TransactionalMemoryStore (2026-05-23)

See [ADR 0014](./docs/adr/0014-versioned-and-transactional-on-durable-adapters.md).
Closes the BL-124 remainder: brings the MVCC content-hash version
Protocol to the durable network adapters and adds a new
`TransactionalMemoryStore` Protocol for native multi-key transactions.
Additive: defaults reproduce prior behaviour byte-for-byte; `S3Store`
stays excluded for the same reason it does not implement CAS.

### Added

- `memory.RedisStore.read_versioned` / `write_versioned` /
  `delete_versioned` (`BL-180`). WATCH/MULTI/EXEC mirror of
  `compare_and_set` with the precondition switched to a content-hash
  comparison. Persistent contention exhausts the bounded retry budget
  and returns `None` / `False` per the BL-072 best-effort convention.
- `memory.DynamoDBStore.read_versioned` / `write_versioned` /
  `delete_versioned` (`BL-180`). One-round-trip conditional PUT/DELETE
  against a server-stored `ver` attribute (the content-hash of the
  value at write time). `read_versioned` hashes the live `v` for
  path-independence; `write_versioned` and `delete_versioned` use
  `ConditionExpression = "ver = :e AND (attribute_not_exists(exp) OR
  exp >= :now)"`. The `exp >= :now` live boundary matches `_live_item`
  (BL-157 / BL-177 / BL-188 expiry-class).
- `memory.TransactionalMemoryStore` Protocol + `memory.TxnWrite` and
  `memory.TxnDelete` frozen dataclasses (`BL-180`). Atomic multi-key
  version-gated transactions: each operation carries an
  `expected_version` referencing the same content-hash token; the
  transaction commits iff every precondition holds, otherwise it is a
  no-op (`transact` returns `None`). An empty transaction returns `{}`;
  a key in both `writes` and `deletes` is rejected at the contract
  boundary as a caller bug.
- `memory.InMemoryStore.transact` (lock-serialized reference impl;
  `BL-180`).
- `memory.SQLiteStore.transact` (one `BEGIN IMMEDIATE` per call, per-key
  precondition check then per-key apply, `ROLLBACK` on a miss; `BL-180`).
- `memory.RedisStore.transact` (`WATCH(all keys)` / sequential `GET`s /
  hash check / `MULTI` / queued commands / `EXEC`, with `WatchError`
  bounded retry; `BL-180`).
- `memory.DynamoDBStore.transact` (one `transact_write_items` call with
  a per-item `ConditionExpression`; the AWS 100-item hard limit is
  pre-checked at the contract boundary;
  `TransactionCanceledException` whose `CancellationReasons` are all
  `ConditionalCheckFailed` is the no-op signal, any other cancellation
  code propagates; `BL-180`).
- `memory.wrap_acl` gains `_ACLTransactionalMixin` so an ACL-wrapped
  transactional backend keeps `isinstance(wrapped,
  TransactionalMemoryStore)` truthful (BL-156 contract). The guard runs
  per touched key before the inner call; an unauthorised op raises
  `AccessDenied` and aborts the whole transaction (all-or-nothing).
- `tests/memory/test_bl124_versioned.py` is now parametrised over all
  four backends (`inmemory`, `sqlite`, `redis`, `dynamodb`); the new
  `tests/memory/test_bl180_transactional.py` covers the transactional
  Protocol with the same parametrisation. A
  `test_write_versioned_against_legacy_row_without_ver_attribute`
  regression covers the documented DynamoDB legacy-row contract.

### Changed

- `memory.DynamoDBStore._item` now stamps `ver = sha256(value)` on
  every write path (`write`, `mset`, `compare_and_set`,
  `write_versioned`, transactional `Put`). The attribute is consistent
  with `v` by construction; pre-BL-180 rows continue to round-trip
  through the existing `_live_item` path and remain readable via
  `read` / `mget` / `list_keys` / `scan` / `read_versioned`. A
  pre-BL-180 row without `ver` cannot be `write_versioned`-updated
  until a single plain `write()` upgrades it (the documented migration
  contract; `LIMITATIONS.md` L17).

### Documentation

- New `docs/adr/0014-versioned-and-transactional-on-durable-adapters.md`.
- `memory/README.md`: removed the "tracked remainder" note for the
  durable Versioned adapters; added a row for `TransactionalMemoryStore`
  and the `wrap_acl` forwarding + `wrap_encrypted` non-forwarding
  rationale.
- `STATUS.md`: new phase row for `BL-180` / ADR 0014.
- `LIMITATIONS.md`: new L17 documenting the DynamoDB legacy-row
  migration contract; the L5 / L12 references unchanged.

## [Unreleased] CI gate hardening: dependency-audit env + disputed pyjwt CVE (2026-05-20)

A CI-policy fix folded into PR #47: the `dependency-audit` job started
failing on every run (including `main`) once `uvx pip-audit` defaulted
its dry-run env to Python 3.11. Build-pipeline change only; no code
or library surface affected.

### CI

- `.github/workflows/ci.yml`: the `dependency-audit` job now invokes
  `uvx --python 3.12 pip-audit ...`, matching the project's
  `requires-python = ">=3.12"`. Without the pin, uvx picked 3.11 and
  the marker `python_version < "3.12"` on `backports.tarfile` (a
  transitive of `jaraco-context`) became true, pulling an unpinned
  `>=` constraint that pip refuses under `--require-hashes`. The audit
  env must match what is actually installed. (`BL-194`)
- Same step adds `--ignore-vuln PYSEC-2025-183` (CVE-2025-45768) with
  an inline rationale: a maintainer-disputed advisory against `pyjwt`
  with no fix version published, reachable here only as a deep
  transitive (`mcp` -> `pydantic-ai-slim` -> `pydantic-ai`); no JWT
  code path is exercised. Revisit trigger documented in the workflow
  comment (advisory withdrawal, replacement CVE, or a hardened pyjwt
  default). (`BL-194`)

## [Unreleased] Approval-resume argument binding (2026-05-20)

PR #46 (`a511760`). A post-ADR-0013 security fix to the runtime
adapter's approval-resume path: a stale resolved approval for one set
of arguments could satisfy a different call to the same tool on
resume. Additive (the new match condition is a strict narrowing of the
prior over-permissive one); regression test added.

### Security

- `harness.runtime._resolved_decision` now binds an in-progress
  approval lookup by the full `(tool, arguments)` tuple, not by `tool`
  alone: an approval previously granted for
  `risky({"path": "approved.txt"})` no longer authorises a fresh
  `risky({"path": "victim.txt"})` after a pause. The default
  `HarnessToolGuard` mints a new `interruption_id` per check, so the
  id is not a stable cross-pause binding key on its own; the docstring
  records this. Regression test:
  `tests/harness/test_runtime_adapter.py::test_gate_resume_does_not_reuse_stale_approval_for_new_arguments`.
  Authorization-boundary fix on the L1 / L2 approval-resume path
  (`BL-001`, `BL-002`). (`BL-193`)

## [Unreleased] Fifth code audit: additive hardening (2026-05-19)

See [ADR 0013](./docs/adr/0013-fifth-code-audit.md). A fifth in-depth
audit targeting the classes the prior audits fixed pointwise and the
paths the recent major dependency bumps exercise. Additive with
regression tests; defaults reproduce prior behaviour for every
non-adversarial input.

### Fixed

- `InMemoryStore` / `SQLiteStore` `list_keys` and `scan` now use the
  same `now <= expires_at` live boundary as `read` / `sweep_expired`;
  a key at the exact expiry instant that `read` still returns is no
  longer missing from a listing for one tick (the read-vs-CAS boundary
  class, unfixed for the listing paths of the in-tree reference
  adapters; BL-168's fix comment, which wrongly asserted agreement, is
  corrected). (`BL-188`, ADR 0013)
- `OpenAIBatchProcessor.results` surfaces a structured `error.code`
  for an output-file row with `response: null` and an `error`
  (previously yielded an uninformative `http_None`, dropping the
  diagnostic on a billing-relevant bulk path). (`BL-189`, ADR 0013)
- `_balanced_spans` caps the recorded span list at `_MAX_SPANS`, so a
  bracket-heavy untrusted body cannot amplify into an unbounded list of
  index pairs (a memory axis the BL-173/182 parse-work bounds did not
  cover); overflow degrades to the existing malformed-input contract.
  (`BL-191`, ADR 0013)
- `scripts/check_run_records.py` validates `--registry` values as
  canonical lowercase 64-hex at load; a non-canonical registry is now
  a clear invocation failure (exit 2) naming the bad keys instead of
  making the gate silently unsatisfiable. (`BL-192`, ADR 0013)

### Security

- `LocalSkillSource.fetch` clears `dest/<name>` through the shared
  symlink-safe `_prepare_install_dir` (defence in depth and a clean
  `SkillLoadError` instead of an unhandled `OSError` on a pre-existing
  symlink; the BL-172 "twin", finally propagated). Not an escape;
  consistency / robustness hardening. (`BL-190`, ADR 0013)

### Documentation

- ADR 0013 added; the ADR index gains the missing `0012` row and the
  new `0013` row; `docs/backlog.md` tracks `BL-188`-`BL-192`; CLAUDE.md
  ADR enumeration extended to `0012`/`0013`.

## [Unreleased] Cross-repo review: run provenance + Anthropic capabilities (2026-05-17)

See [ADR 0012](./docs/adr/0012-run-provenance-and-anthropic-capabilities.md).
Additive: a new opt-in `record_sink` keyword on `run_under_contract`,
three new harness modules (`provenance`, `anthropic_api`, `openai_api`),
the `scripts/check_run_records.py` gate, and two optional extras
(`anthropic`, `openai`); defaults reproduce prior behaviour.

### Added

- `harness.RunRecord` / `contract_digest` / `verify_run_record` and a
  `record_sink` keyword on `run_under_contract`: a schema-versioned,
  self-attesting record stamped at the run's terminal point with the
  in-process digest of the contract that actually enforced it (not
  reconstructed from git, the explicit divergence from the `sentinel`
  provenance approach). `scripts/check_run_records.py` re-validates a
  persisted corpus with hard errors only (no warn-and-pass tier).
  `docs/schema/run-record.json` is gen-schema guarded. (`BL-185`,
  ADR 0012)
- `harness.AnthropicBatchProcessor` (Message Batches: async bulk at
  50% token price, dependency-injected client, lazy `from_env`) and
  `harness.cache_control_system` (prefix-stable prompt-cache block),
  behind a new optional `anthropic` extra; the module imports and
  type-checks with the SDK absent. (`BL-186`, ADR 0012)
- `harness.OpenAIBatchProcessor`: the OpenAI counterpart (JSONL
  upload, batch create, JSONL output/error decode), behind a new
  optional `openai` extra, same injected-client + lazy `from_env`
  design. `OpenAIBatchRequest.model` is required (no guessed default).
  (`BL-187`, ADR 0012)

## [Unreleased] Third audit + L3 capability wave (2026-05-17)

See [ADR 0011](./docs/adr/0011-third-audit-and-l3-capability-wave.md).
All changes are additive to the L1 Protocols (new optional keywords /
modules / side-by-side Protocols; defaults reproduce prior behaviour).

### Added

- `memory.EnvKeyProvider` / `memory.FileKeyProvider` (single key,
  base64/hex/raw, stdlib only); the `memory.VersionedKeyProvider`
  Protocol and `memory.RotatingKeyProvider` reference; `EncryptedStore`
  writes a rotation-safe key-id value envelope over a versioned
  provider so a rotation does not strand prior ciphertext (a plain
  `KeyProvider` keeps the exact prior on-disk format). (`BL-111`)
- `memory.AttributeACL` / `memory.AttributeRule` (attribute-based
  access decided per call) and a `harness` `AccessDenied` event
  (exported as `AccessDeniedEvent`) emitted by `ACLStore` / `wrap_acl`
  before raising when the optional audit surface is supplied.
  (`BL-122`)
- `memory.SemanticMemoryStore` extension Protocol + `SemanticHit` and
  `memory.InMemorySemanticStore` (with `memory.Embedder`), a
  deterministic in-tree vector store reusing the BL-110
  `HashingEmbeddingProvider`. (`BL-131`)
- `memory.VersionedMemoryStore` extension Protocol
  (`read_versioned` / `write_versioned` / `delete_versioned`, a
  content-hash MVCC token) with `InMemoryStore` and `SQLiteStore`
  reference impls. (`BL-124`)
- A top-level `evaluation/` component (`evaluate_dispatch` P@1 / MRR,
  `evaluate_trajectory`, golden-set loader, metrics), the in-tree
  golden set, `scripts/eval.py`, and a blocking CI `evaluation` job in
  the `ci-success` aggregate (mypy and the coverage target now include
  `evaluation`). (`BL-130`)
- `run_under_contract(..., parent_span_id=...)` for a correlated
  nested-run span tree (None preserves the prior flat behaviour).
  (`BL-176`)
- ADR 0011.

### Fixed

- A pre-existing `dest/<name>` symlink let `GitHubSkillSource` /
  `MarketplaceSkillSource` escape the install directory (it resolved
  before clearing the link); a hardened `_prepare_install_dir` unlinks
  the link first and asserts containment (the network-source twin of
  `BL-169`). (`BL-172`)
- `_balanced_spans` was O(n^2) on nested `[[[...]]]` model output
  (per-close substring slices); now O(1) index pairs with a capped
  lazy parse, and `RecursionError` from a deep span is contained in
  the extractor and the LLM / skill-based dispatcher boundary
  (DispatchError contract kept). (`BL-173`)
- `compose_contracts` governance keeps the strictest predicate (HARD
  over SOFT) on a name collision, not first-occurrence (the governance
  analogue of `BL-166`; a reviewed HARD veto was silently downgraded).
  (`BL-174`)
- A postcondition retry directive re-recorded every predicate into the
  `DriftMonitor`; postcondition drift is now recorded exactly once per
  run. (`BL-175`)
- `DynamoDBStore.compare_and_set` (match branch) / `compare_and_delete`
  gate on `exp >= :now`, matching `_live_item`'s live boundary (was
  `> :now`, CAS-absent at the exact expiry instant). (`BL-177`)
- `SQLiteStore.mset` / `mdelete` of an empty batch is an early no-op
  (was taking the write lock for nothing). (`BL-178`)

### Changed

- `RetryPolicy` docstring corrected: token/step usage is charged from
  the final attempt only (PydanticAI raises without partial usage on a
  failed run); the gap is tracked. (`BL-179`, `LIMITATIONS.md` L15)

### PR #28 review follow-ups

- `BL-181`: authenticated legacy-ciphertext fallback so adopting a
  `VersionedKeyProvider` on a plain-provider store stays readable
  (AES-GCM authenticated; migration contract in `LIMITATIONS.md` L16).
- `BL-182`: `first_json_array` bounds parse *work* (oversized-span
  skip + cumulative byte budget, `continue` not `break`), not candidate
  count, so a valid array after many leading bracket fragments is found
  while the BL-173 DoS bound holds.
- `BL-183`: `evaluate_trajectory` classifies `paused` (ResumableState)
  and `approval_denied` outcomes instead of mis-scoring/aborting;
  `wrap_acl` forwards `VersionedMemoryStore`; `InMemorySemanticStore`
  query is safe under concurrent vector removal.
- `BL-184`: review-polish trio: `_balanced_spans` docstring matches the
  current byte-budget constants; `_prepare_install_dir` clears a
  pre-existing regular file (not only a directory); `_decode_key`
  surfaces a clear `ValueError` for a malformed base64/hex/utf-8 key.

### Documentation

- ADR 0011; `docs/adr/README.md`; backlog statuses (`BL-111`, `BL-122`,
  `BL-124`, `BL-130`, `BL-131` resolved; `BL-172`-`BL-179` added;
  `BL-180` added as the BL-124 multi-key remainder);
  `STATUS.md` / `LIMITATIONS.md` refreshed (L5 narrowed, L6 narrowed,
  L15 added); component READMEs and `CLAUDE.md` layout (the
  `evaluation/` component).

## [Unreleased] L3 default-path wiring + audit wave (2026-05-17)

See [ADR 0010](./docs/adr/0010-l3-default-path-wiring-and-audit-wave.md).
All changes are additive to the L1 Protocols (new optional keywords /
modules; defaults reproduce L1/L2 behaviour).

### Added

- Default-path wiring on `run_under_contract` (all opt-in): `skill_contracts`
  composition (`BL-100`), `drift_monitor`/`drift_threshold` with a new
  `DriftThresholdCrossed` event (`BL-101`), recovery directives via
  `RecoveryOutcome.directive` (`BL-102`), and run-scoped `lifecycles`
  (`BL-104`).
- `skills.dispatchers.default_dispatcher`: the recommended instrumented,
  cheap-first chain in one call (`BL-103`).
- `skills.HashingEmbeddingProvider`: a deterministic, dependency-free
  `EmbeddingProvider` so `EmbeddingDispatcher` works with no vendor
  (`BL-110`).
- `ActionBudget.max_cost_usd` / `max_tokens_per_tool` /
  `max_wall_clock_seconds_per_tool`; `BudgetTracker.consume_cost`,
  `snapshot`, and per-tool token/second attribution; `initial_*`
  seeding for a cumulative resume (`BL-123`, `BL-154`).
- `harness.RetryPolicy` and `PydanticAIRuntime(retry_policy=...,
  soft_reject_as_error=...)` (`BL-136`, `BL-137`).
- `skills.MarketplaceSkillSource`, a `SignatureVerifier` hook on the
  network sources, and one shared hardened download/extract path
  (`BL-112`).
- `workloads.load_workload_from_entry_point` (installed-package
  workloads via `[project.entry-points]`) (`BL-121`).
- `agents run --json` and `agents skills install <name> --from <src>`
  (`BL-125`); the CLI honours a model-free manifest dispatcher
  (`BL-161`).
- `memory.wrap_acl` / `memory.wrap_encrypted`: decorators that forward
  the extension Protocols the wrapped backend supports, truthfully
  (`BL-156`).
- Governance/release: tree-wide `REUSE.toml` + `LICENSES/Apache-2.0.txt`
  with a `reuse lint` CI gate (`BL-152`); a blocking `dependency-audit`
  CI job (`BL-150`); `docs/releasing.md` and `.github/workflows/release.yml`
  (`BL-151`); ADR 0010.

### Fixed

- `HarnessToolGuard`: a SOFT governance failure now returns REJECT/SOFT
  instead of APPROVE (it was a silent no-op; the runtime's soft-reject
  path was dead). (`BL-163`)
- A raising `RecoveryHandler` is contained (`recovered=False`,
  continue) instead of aborting a soft path. (`BL-164`)
- `PydanticAIRuntime.run` re-raises `CancelledError`/`BudgetExceeded`
  before consulting guard state. (`BL-165`)
- `compose_contracts` keeps the strictest predicate (HARD over SOFT) on
  a name collision, not first-occurrence. (`BL-166`)
- `MemoryAudit` rejects reserved base-event keys (e.g. `namespace`) at
  construction, not mid-run. (`BL-167`)
- `SQLiteStore.sweep_expired` uses strict `<`, consistent with
  read/list/scan. (`BL-168`)
- `LocalSkillSource` copies regular files only and refuses a symlink
  (was dereferencing into the bundle). (`BL-169`)
- `S3Store.scan` / `DynamoDBStore.scan` page past non-terminal empty
  pages instead of falsely signalling exhaustion; `S3Store.list_keys`
  pushes the prefix server-side; `SQLiteStore` batch ops are atomic;
  `SkillRegistry` `name@version` parses via `rpartition`; non-file
  archive members are rejected; per-member reads clamp to the remaining
  budget; `DynamoDBStore` `exp` is float seconds. (`BL-157`, `BL-161`,
  `BL-170`)

### Changed

- `EncryptedStore.read`/`write` route through shared `_seal`/`_unseal`
  helpers (behaviour unchanged); `install_skill`/registry gain an
  `allow_contract` passthrough.

### Documentation

- ADR 0010; `docs/releasing.md`; backlog statuses (`BL-100`-`BL-104`,
  `BL-110`, `BL-112`, `BL-121`, `BL-123`, `BL-125`, `BL-136`, `BL-137`,
  `BL-150`-`BL-152`, `BL-154`, `BL-156`, `BL-157`, `BL-161` resolved;
  `BL-163`-`BL-171` added); `STATUS.md`/`LIMITATIONS.md` refreshed
  (L4, L5, L9, L10, L12-L14). Dispatcher-count erratum: "seven routers
  plus an InstrumentedDispatcher wrapper" (ADR 0009 said "eight",
  internally contradictory; ADRs are immutable, corrected forward).
  `docs/runtime-providers.md` PydanticAI doc URLs and shifted line
  citations corrected.

## [Unreleased] Skill bundles: shell and routing (2026-05-17)

### Added

- `skills/shell/`: a skill for authoring robust, safe Bash and running
  commands reliably on local and remote machines (SSH-first; a decision
  ladder of direct exec, `setsid`/`systemd-run`, and `expect`, with no
  screen-scraping rung). Ships references and a ShellCheck-clean
  Bash skeleton asset.
- `skills/dispatcher-skill/`: the versioned routing skill whose body is
  the dispatch prompt for `SkillBasedDispatcher`. Makes the recommended
  default dispatcher composition in ADR 0006 runnable as written
  (previously the composition referenced a skill that did not ship).

## [Unreleased] Code audit: correctness, security, documentation (2026-05-17)

### Fixed

- `skills.embeddings.cosine_similarity`: a non-finite component (NaN or
  inf, e.g. a buggy provider or adversarial skill text) returned NaN,
  which `min`/`max` clamping turned into confidence `1.0`, sorting a
  poison skill to the top of embedding dispatch. Now returns `0.0`.
  (`BL-159`)
- `skills.dispatchers._json.first_json_array`: the bracket scanner was
  O(n^2) on adversarial model output (a megabyte of `[` hung the
  dispatcher). Rewritten as a single linear pass. (`BL-159`)
- LLM and skill-based dispatchers: a JSON `confidence: true` was
  accepted (`isinstance(True, int)`) and coerced to `1.0`; booleans are
  now rejected. (`BL-159`)
- `memory.EncryptedStore` / `memory.ACLStore`: validate keys before any
  keyed operation, per the `MemoryStore` Protocol. For `EncryptedStore`
  this also prevents a `::` key from colliding AAD across keys.
  (`BL-159`)

### Changed

- `harness.redaction.Redactor` now scrubs every event field, not only
  dict-valued ones, so a secret-shaped or over-long value in a
  top-level string or list field is also redacted. Strict superset of
  the prior behaviour. (`BL-159`)

### Security

- `SECURITY.md`: added the "Untrusted content and prompt injection"
  posture (tool results, MCP output, skill bodies, memory values, and
  model output are untrusted; the authority boundary is the harness,
  not the prompt) and documented the out-of-tree workload code-execution
  boundary and the wall-clock-watchdog caveat. (`BL-139` resolved)

### Documentation

- ADR 0009 records the audit's cross-cutting decisions and the ADR
  0005/0006 factual errata (immutable ADRs noted forward, not edited).
- `LIMITATIONS.md` L10-L14 (budget non-cumulative across resume;
  watchdog preempts only at await; decorators do not forward extension
  Protocols; DynamoDB integer-second TTL; out-of-tree workload code
  execution). `docs/backlog.md`: `BL-154`-`BL-162` added; provenance
  date refreshed. `BL-162` / ADR 0009 section 5 record the ADR 0008
  section 4 follow-up: `main` branch protection must be repointed from
  the stale `test` context to `ci-success` (a Settings change, not a
  file; repoint, do not relax the gate).
- Fixed factual drift: README dispatcher count (eight, not "seven");
  `docs/runtime-providers.md` stale line citations; the `BL-130` ->
  `BL-134` reference in `harness.redaction`; `workloads.manifest`
  "Phase 5" tense; the "five reference dispatchers" docstrings; the
  watchdog "preemptive" wording. `CLAUDE.md`: the markdown `--`/em-dash
  rule now exempts code spans (it previously contradicted its own docs).

## [Unreleased] L3 Tier 0: security hardening and roadmap (2026-05-17)

### Added

- `harness.redaction`: `Redactor` and `RedactingSink` scrub sensitive
  argument names, secret-shaped values, and over-long scalars from
  events before they reach a sink. Exported from `harness`. (`BL-134`)
- `skills.sources.GitHubSkillSource`: keyword-only `sha256`,
  `max_download_bytes`, `max_members`, `max_file_bytes`,
  `max_total_bytes` bound a hostile or corrupt archive and optionally
  verify the tarball digest. (`BL-112`, partial)
- `skills.loader.discover_skill`: `allow_contract` parameter; gates
  execution of a skill's `contract.py`. (`BL-133`, partial)
- Governance docs: `STATUS.md`, `LIMITATIONS.md`, `CHANGELOG.md`,
  `docs/adr/README.md`, expanded `CONTRIBUTING.md`. (`BL-153`)
- ADR 0008; CodeQL workflow; Python 3.13 added to the CI test matrix.
- `docs/backlog.md`: restructured into priority tiers; `BL-130`-`BL-153`
  added from the deep analysis and validated against primary sources.

### Changed

- `skills.sources.install_skill` now defaults `allow_contract=False`: an
  installed bundle's `contract.py` is not executed unless the caller
  opts in. Intentional secure default at the network trust boundary;
  the L1 `discover_skill` default is unchanged. See ADR 0008.

### Security

- Closed the unbounded-download and decompression-bomb exposure in the
  GitHub skill source and the auto-execution of untrusted skill
  contracts. Closed plaintext secret leakage into event sinks. Residual
  risk (gate is not a sandbox) is recorded in `LIMITATIONS.md` L3.

## [L2] Implementation wave (2026-05-16)

Guard and budget wiring into the PydanticAI adapter, durable memory
backends (Redis, SQLite, S3, DynamoDB), observability surface,
contract composition, recovery handlers, JSD drift, skill install.
Delivered as PR #20 (merge `af1df9d`); follow-ups PR #21, PR #22
(coverage gate at 94%). All `BL-001`-`BL-090` resolved. See ADR 0007
and `docs/backlog.md`.

## [L1] Framework (2026-05-16 and earlier)

Contract surface and enforcement, action budgets, memory namespace
contract with `InMemoryStore`, workload bundles and loader, skills and
the dispatcher framework, the `Runtime` Protocol with a PydanticAI
default. See ADR 0001-0006.
