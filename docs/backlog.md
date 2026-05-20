# L2 Backlog

Consolidated from ADRs 0002 through 0006. Generated 2026-05-16 after Phase 5 (`7c26543`). Updated 2026-05-16 after PR #20 merged to `main`; extended 2026-05-17 (ADR 0008 L3 roadmap, the ADR 0009 code-audit items `BL-154`-`BL-162`, then the ADR 0010 L3 default-path-wiring wave: `BL-100`-`BL-104` resolved, the well-scoped follow-ups and Tier 1/2/3/4 items resolved, and the second-audit items `BL-163`-`BL-171`); extended again 2026-05-17 (the ADR 0011 third-audit + L3 capability wave: `BL-111`, `BL-122`, `BL-130`, `BL-131` resolved, `BL-124` resolved with the multi-key-transaction remainder tracked as `BL-180`, and the third-audit items `BL-172`-`BL-179`). Extended 2026-05-19 (ADR 0012 cross-repo provenance + provider-batch items `BL-185`-`BL-187`, and the ADR 0013 fifth-audit items `BL-188`-`BL-192`).

## Implementation status

All 35 items (BL-001 .. BL-090) are **implemented, tested, and merged to
`main`** via PR #20 (merge commit `af1df9d`, 2026-05-16), delivered as
reviewable batches with the rationale in
[ADR 0007](./adr/0007-l2-implementation-wave.md). Per the Maintenance
convention below, every L2 item is therefore `[resolved]`; the single
merge commit `af1df9d` (PR #20) is the resolution reference for all of
them, so it is stated here once rather than repeated per line.
Follow-ups merged after the wave: README formatting (PR #21,
`5558b88`) and CI coverage enforcement at `cov-fail-under=94` (PR #22,
`573009d`).

Where each item landed (module / public symbol):

- BL-001..004, 073: `harness.runtime.PydanticAIRuntime` (guard gate,
  `ResumableState` resume, wall-clock watchdog, streaming budget),
  `harness.budgets` per-tool quotas.
- BL-010..013: `workloads.loader`, `harness.ToolCatalog`,
  `skills.validators`, `scripts/gen_schema.py` + `docs/schema/`.
- BL-020..022: `agents` CLI package.
- BL-030..033: `memory.{redis,sqlite,s3,dynamodb}`.
- BL-040, 080..083, 072: `memory._audit`, `memory.store` extension
  Protocols, `memory.sweep`, `InMemoryStore`/adapters.
- BL-041, 042: `harness.OTelSink`, `skills.InstrumentedDispatcher`.
- BL-050..054: `skills.dispatchers.{multi,embedding}`,
  `skills.embeddings`, skill `contract()`, registry versioning,
  `skills.sources`.
- BL-060..062: `harness.compose_contracts`, `harness.recovery`,
  `harness.drift`.
- BL-070, 071: `memory.EncryptedStore`, `memory.ACLStore`.
- BL-090: `workloads.load_workload_from_path`.

## Conventions

Each item has an ID, status, size estimate, source ADR, and notes.

- Status: `pending`, `in-progress`, `blocked`, `resolved`.
- Size: `XS` (~30 min), `S` (~1 to 2 h), `M` (~half day), `L` (~day or more).
- IDs are stable; do not renumber on removal. Use `resolved` instead of deleting.

## Adapter integration

`HarnessToolGuard` and `BudgetTracker` shipped in Phase 2 with full surfaces and event emission, but at L1 neither was wired into the default `PydanticAI` adapter's tool-call path. The L2 wave closed this L1-to-L2 bridge (BL-001..004, 073): the adapter now gates every local and MCP tool call through the guard and feeds usage into the budget tracker. See [docs/runtime-providers.md](./runtime-providers.md) for how a workload reaches a model through this adapter.

- `BL-001` [resolved] [S] Wire `HarnessToolGuard` into the PydanticAI adapter so tool calls hit `guard.check(tool, arguments)` before execution and respect `REJECT`, `REQUIRE_APPROVAL`, and `APPROVE` decisions. (ADR 0002, ADR 0003)
- `BL-002` [resolved] [M] Live interruption-resume mid-run in the PydanticAI adapter: an `ApprovalInterruption` should pause the run, surface `ResumableState`, and resume cleanly on `.approve()` or `.deny()`. Lands with the first workload that needs human-in-the-loop approval. (ADR 0003)
- `BL-003` [resolved] [S] Background watchdog for wall-clock budget enforcement. Currently `BudgetTracker` checks at step boundaries; long-running tools can exceed `max_wall_clock_seconds` without preemption. (ADR 0003)
- `BL-004` [resolved] [S] Streaming budget enforcement: accumulate token usage during a stream and raise `BudgetExceeded` on threshold cross. (ADR 0003)

## Workload + skill validators

Phase 5's `SkillRegistry` unblocked several Phase 4 validators.

- `BL-010` [resolved] [XS] Workload loader: validate that `manifest.name` matches the package directory name. Silent mismatch today. (ADR 0005)
- `BL-011` [resolved] [S] Workload loader: validate that every `skills:` entry resolves in a `SkillRegistry`. Optional dependency: the registry must be passed at load time. (ADR 0005, ADR 0006)
- `BL-012` [resolved] [S] Validate skill `allowed-tools` entries against the harness's known tool catalog. (ADR 0006)
- `BL-013` [resolved] [S] Manifest JSON Schema generation (`WorkloadManifest.model_json_schema()`) emitted to `docs/schema/workload-manifest.json` for editor autocomplete. (ADR 0005)

## CLI surface

- `BL-020` [resolved] [S] `python -m agents workloads list` -> prints every loadable workload's name, version, description. (ADR 0005)
- `BL-021` [resolved] [M] `python -m agents run <workload> <query>` -> loads the workload, dispatches via its configured `Dispatcher`, runs under contract, prints structured result. (ADR 0006)
- `BL-022` [resolved] [S] `python -m agents skills list` -> prints every skill in `skills/`, grouped by lane.

## Memory adapters

L1 shipped only `InMemoryStore`. The L2 wave added the durable backends below.

- `BL-030` [resolved] [M] Redis adapter. Pipelining for batch ops, WATCH/MULTI for atomic CAS, native TTL via `PX`. (ADR 0004)
- `BL-031` [resolved] [M] SQLite adapter for durable single-host workloads. WAL mode, per-namespace tables. (ADR 0004)
- `BL-032` [resolved] [M] S3 adapter for blobs and audit packs. Eventually-consistent; document the semantics deviation. (ADR 0004)
- `BL-033` [resolved] [L] DynamoDB adapter for AWS-native deployments. Strongly-consistent reads optional. (ADR 0004)

## Observability

- `BL-040` [resolved] [S] Memory operation events (`MemoryRead`, `MemoryWrite`, `MemoryDelete`) emitted through `EventSink`. Surface in `harness.events` is ready; the `MemoryStore` Protocol needs an optional `sink` parameter. (ADR 0004)
- `BL-041` [resolved] [S] OTel-Collector-compatible `EventSink` implementation. `HarnessEvent` already carries `trace_id`, `span_id`, `parent_span_id`. (ADR 0002)
- `BL-042` [resolved] [M] Dispatch performance instrumentation: per-dispatcher latency histograms, runtime token consumption, threshold-fallback rate. Feeds Grafana via OTel. (ADR 0006)

## Skill ecosystem

- `BL-050` [resolved] [S] `MultiDispatcher` ensemble that combines results from several dispatchers via vote, average, or weighted blend. (ADR 0006)
- `BL-051` [resolved] [M] Embedding-based dispatcher. Vector similarity between query and skill descriptions. Requires an embedding adapter or a Runtime that exposes embeddings. (ADR 0006)
- `BL-052` [resolved] [M] Skill-level contracts (`skills/<name>/contract.py`) that compose with the workload contract. Composition rule: intersection of predicate sets. (ADR 0006)
- `BL-053` [resolved] [M] Skill versioning and rollback. Track multiple versions of the same skill; load by `name@version`. (ADR 0006)
- `BL-054` [resolved] [L] Skill installation: the `SkillSource` Protocol plus `LocalSkillSource` and `GitHubSkillSource`. Scope clarification: this delivered the Protocol surface and the two stdlib sources. The Vercel `skills.sh` marketplace source and checksum/signature verification were not part of BL-054; they are tracked as `BL-112`. (ADR 0006)

## Composition (Bhardwaj agent-contract tuple)

- `BL-060` [resolved] [M] Workload + skill contract composition. Intersection of predicate sets, governance union, approval-required union. (ADR 0002)
- `BL-061` [resolved] [M] Recovery handlers for soft violations: the R in the Bhardwaj tuple. Predicates today flag-and-emit; recovery actions are unspecified. (ADR 0002)
- `BL-062` [resolved] [L] JSD distributional drift instrumentation across runs. Aggregated state distribution per predicate. (ADR 0002)

## Production hardening

- `BL-070` [resolved] [M] Encryption at rest for memory adapters. Per-adapter concern; the framework should provide a `KeyProvider` Protocol. (ADR 0004)
- `BL-071` [resolved] [M] ACL / role-based per-key access controls on `MemoryStore`. The contract layer covers workload-boundary auth; per-key ACLs are an L2 refinement. (ADR 0004)
- `BL-072` [resolved] [L] CAS / MVCC primitives in adapters that support them. Exposed via a separate `CASMemoryStore` Protocol so non-CAS backends do not have to fake it. Protocol + InMemoryStore reference impl landed; per-adapter impls land with each adapter. (ADR 0004)
- `BL-073` [resolved] [S] Per-tool quotas (e.g. up to 3 calls to `search`, up to 1 call to `delete`). Currently a single `max_tool_calls` counter applies to all. (ADR 0003)

## Memory convenience

- `BL-080` [resolved] [S] Active TTL sweep background task for `MemoryStore` adapters that benefit from it. `InMemoryStore` uses lazy expiry today. (ADR 0004)
- `BL-081` [resolved] [S] Multi-key batch operations: `mget(keys)`, `mset(items)`, `mdelete(keys)`. (ADR 0004)
- `BL-082` [resolved] [M] Iterator-style `list_keys` for very large keyspaces. Cursor-based; bounded result pages. (ADR 0004)
- `BL-083` [resolved] [S] Content addressing: `write_content(value) -> sha256-hex-key`. Useful for immutable storage patterns. (ADR 0004)

## Workload convenience

- `BL-090` [resolved] [M] Out-of-tree workloads: load from an arbitrary filesystem path, not just the `workloads/` package tree. Scope clarification: filesystem-path loading shipped here; loading from an installed package via `[project.entry-points]` was not part of BL-090 and is tracked as `BL-121`. (ADR 0005)

# L3 backlog

Added 2026-05-16. Consolidated from ADR 0007's revisit triggers and the
deferrals recorded in L2 code/docstrings. Extended 2026-05-17 (ADR 0008)
with `BL-130`-`BL-153` from a deep analysis against current
agent-engineering practice, each cross-checked against a primary source
(see "Sources consulted"). Same conventions as above; IDs use the
`BL-1xx` range so they do not collide with L2 (`BL-0xx`).

L3 is not "more breadth": L2 shipped the primitives, L3 wires them into
the default execution path, supplies real implementations behind the
pluggable Protocols, and closes the practice gaps the analysis found.

### Priority tiers

The original L3 clusters (default-path wiring, real implementations,
reference workload) keep their IDs and text below. ADR 0008 groups all
open L3 work into delivery tiers:

- Tier 0, security: `BL-112`, `BL-133`, `BL-134`, `BL-150`. `BL-134`
  resolved; `BL-112` resolved (ADR 0010: marketplace source + signature
  hook delivered); `BL-150` partial (blocking dependency-audit gate
  delivered; commit-SHA pinning is the tracked remainder); `BL-133`
  in-progress.
- Tier 1, AI quality and safety: `BL-130`, `BL-131`, `BL-132`,
  `BL-137`, `BL-139`, plus `BL-110`, `BL-120`. `BL-110`, `BL-137`,
  `BL-139` resolved (ADR 0010 / ADR 0009); `BL-130`, `BL-131` resolved
  (ADR 0011).
- Tier 2, reliability and observability: `BL-135`, `BL-136`, `BL-138`,
  plus `BL-100`-`BL-104`, `BL-113`, `BL-123`. `BL-100`-`BL-104`,
  `BL-123`, `BL-136` resolved (ADR 0010).
- Tier 3, governance: `BL-152`, `BL-153` (both resolved).
- Tier 4, release and operations: `BL-151` (resolved, ADR 0010).

The L3 default-path-wiring wave (`BL-100`-`BL-104`), the well-scoped
ADR 0009 follow-ups, the Tier 1/2 cost/retry/reject items, the
out-of-tree/CLI extensions, and governance/release maturity landed
together; see [ADR 0010](./adr/0010-l3-default-path-wiring-and-audit-wave.md).
Items resolved in that wave cite ADR 0010 and branch
`claude/audit-and-docs-update-2U05F` as the resolution reference (stated
here once rather than per line).

The ADR 0011 third-audit + capability wave then resolved `BL-111`
(key providers), `BL-122` (ABAC + audited denial), `BL-124` (MVCC
version tokens, multi-key transactions tracked forward as `BL-180`),
`BL-130` (the evaluation gate, closing `LIMITATIONS.md` L6), and
`BL-131` (semantic memory, closing the vector-retrieval half of L5),
plus the third-audit fixes `BL-172`-`BL-178` (`BL-179` tracked); see
[ADR 0011](./adr/0011-third-audit-and-l3-capability-wave.md). The
remaining highest-leverage open work is one real live-model workload
(`BL-120`); the rest (`BL-113`/`138` true OTel spans, `BL-114`
non-replay resume, `BL-132`/`171` prompt caching, `BL-135` compaction,
`BL-155` true preemption, `BL-180` multi-key transactions) each need an
unstable upstream or a live model and are deliberately tracked, not
rushed.

## Default-path wiring

The L2 primitives exist but are opt-in / manually engaged. L3 makes the
framework use them by default.

- `BL-100` [resolved] [M] Auto-compose a workload's loaded skill contracts with its workload contract in `run_under_contract`. Delivered: the `skill_contracts` keyword on `run_under_contract` composes via `compose_contracts` before enforcement; default None preserves L1. (ADR 0002, ADR 0006, ADR 0010)
- `BL-101` [resolved] [M] Record predicate pass/fail into a `DriftMonitor` from the enforcement loop and emit a threshold-crossing event. Delivered: `drift_monitor` / `drift_threshold` keywords; new `DriftThresholdCrossed` event. (ADR 0002, ADR 0010)
- `BL-102` [resolved] [M] Recovery control flow: let a `RecoveryHandler` outcome drive retry / substitute / escalate, not just emit-and-continue. Delivered: `RecoveryOutcome.directive` (continue/retry/substitute/escalate), honoured on the postcondition stage; default "continue" preserves L1. (ADR 0002, ADR 0010)
- `BL-103` [resolved] [S] Fold `InstrumentedDispatcher` into the recommended default dispatcher composition. Delivered: `skills.dispatchers.default_dispatcher` (instrumented cheap-first chain). The worked OTel/Grafana example is documentation, tracked forward under `BL-113`/`BL-138`. (ADR 0006, BL-042/050, ADR 0010)
- `BL-104` [resolved] [S] Harness/workload-runner-managed lifecycle (start/stop tied to a run), instead of fully manual opt-in. Delivered: the `lifecycles` keyword on `run_under_contract` enters/exits any async context manager (e.g. a `memory.TTLSweeper`) around the run; dependency-free so the harness does not import `memory`. (ADR 0004, BL-080, ADR 0010)

## Real implementations behind the pluggable Protocols

L2 shipped Protocols plus deterministic test doubles; L3 supplies
production implementations.

- `BL-110` [resolved] [M] A concrete `EmbeddingProvider` so `EmbeddingDispatcher` (BL-051) is usable without a hand-rolled provider. Delivered: `skills.HashingEmbeddingProvider`, deterministic and dependency-free (the hashing trick); a model-quality vendor-backed provider stays out-of-tree by the same no-vendor-binding stance as ADR 0001. (ADR 0006, ADR 0010)
- `BL-111` [resolved] [M] `KeyProvider` beyond `StaticKeyProvider`: env/file and a KMS-backed provider, plus key rotation/versioning for `EncryptedStore` (BL-070). Delivered (ADR 0011): `EnvKeyProvider` / `FileKeyProvider` (single key, base64/hex/raw, stdlib only); the `VersionedKeyProvider` Protocol plus `RotatingKeyProvider` reference; `EncryptedStore` writes a rotation-safe key-id value envelope over a versioned provider (a plain `KeyProvider` keeps the exact prior on-disk format, byte-additive). Scope: the KMS-backed provider stays out-of-tree by the ADR 0001 no-vendor-binding stance (the `BL-110` precedent); the Protocol is the in-tree extension point. (ADR 0004, ADR 0011)
- `BL-112` [pending] [M] Marketplace `SkillSource` (Vercel `skills.sh`) and checksum/signature verification on install. Extends BL-054. (ADR 0006)
- `BL-113` [pending] [L] True OTel spans + trace-context propagation. `OTelSink` (BL-041) emits log records with trace/span as attributes because the OTel logs SDK is unstable; revisit when it stabilizes, add GenAI semantic conventions for streaming. (ADR 0002)
- `BL-114` [pending] [L] Deeper PydanticAI resume via `DeferredToolRequests` / `message_history` instead of re-running the agent on resume. `_resumable` (BL-002) replays today; revisit when PydanticAI's pause/resume primitive is stable. (ADR 0003 revisit trigger)

## Reference workload and loose ends

- `BL-120` [pending] [L] A real reference workload exercising the wired runtime end-to-end against a live model (only `_example` stub exists). Becomes the adapter's CI smoke, gated to skip without API keys.
- `BL-121` [resolved] [S] Out-of-tree workloads from an installed package / `[project.entry-points]`, not just a filesystem path. Delivered: `workloads.load_workload_from_entry_point` (group `agents.workloads`), reusing the shared loader + BL-010/011 validators; same trust boundary as path loading (`LIMITATIONS.md` L14). Extends BL-090. (ADR 0005, ADR 0010)
- `BL-122` [resolved] [S] Attribute-based / dynamic `AccessPolicy`, and an `AccessDenied` audit event through `EventSink`. Delivered (ADR 0011): `AttributeACL` / `AttributeRule` (grants decided per call from principal attributes, not a static role table) and a `harness.events.AccessDenied` event emitted by `ACLStore` / `wrap_acl` before raising when the optional `sink` / `base_event_fields` audit surface is supplied (the BL-040 silent-without-base-fields convention). Exported as `AccessDeniedEvent` (disambiguating the `memory.errors` exception, mirroring `ApprovalDenied`/`ApprovalDeniedEvent`). Extends BL-071. (ADR 0004, ADR 0011)
- `BL-123` [resolved] [M] Cost and per-tool wall-clock / token budgets; the per-tool cap (BL-073) was call-count only. Delivered: `ActionBudget.max_cost_usd` / `max_tokens_per_tool` / `max_wall_clock_seconds_per_tool`, `BudgetTracker.consume_cost` and per-tool token/second attribution on `consume_tool_call` (all opt-in, default None preserves BL-073). (ADR 0003, ADR 0010)
- `BL-124` [resolved] [L] MVCC / version tokens beyond compare-and-set, and multi-key transactions where the backend supports them. Delivered (ADR 0011): the `VersionedMemoryStore` extension Protocol (`read_versioned` / `write_versioned` / `delete_versioned` via a content-hash version token, path-independent so any write changes it) with `InMemoryStore` and `SQLiteStore` reference impls. Not forwarded through `EncryptedStore` for the same per-write-nonce reason CAS is not. Scope clarification: the version-token surface shipped here; multi-key transactions where the backend supports them, and the non-content backends, are tracked as `BL-180` (the BL-072 Protocol-plus-reference-first scoping). Extends BL-072. (ADR 0004, ADR 0011)
- `BL-180` [pending] [L] Multi-key transactions where the backend supports them (SQLite, Redis MULTI, DynamoDB TransactWriteItems), and `VersionedMemoryStore` on the durable network adapters beyond the BL-124 InMemory/SQLite reference. The remainder of BL-124. (ADR 0004, ADR 0011)
- `BL-125` [resolved] [S] `agents run --json` (compact output) and an `agents skills install <name> --from <source>` subcommand (local / github, `allow_contract=False`). Typed-input models / streaming output stay with the live-workload work (`BL-120`). Extends BL-021, BL-054. (ADR 0006, ADR 0010)

## Security hardening (Tier 0)

First increment delivered 2026-05-17 (ADR 0008). The gate is defence in
depth, not a sandbox (`LIMITATIONS.md` L3).

- `BL-112` [resolved] [M] Marketplace `SkillSource` and integrity verification on install. Delivered (ADR 0008): bounded download / member / size caps and optional `sha256`. Delivered (ADR 0010): the hardened download/extract factored into one audited path, a `SignatureVerifier` hook (`signature` / `verify_signature`, framework binds no crypto vendor), and a generic `MarketplaceSkillSource` (configurable URL template, `strip_components`) over the same hardened path. Extends `BL-054`. (ADR 0006, ADR 0008, ADR 0010)
- `BL-133` [in-progress] [M] Skill execution isolation. Delivered: `discover_skill(allow_contract=...)` and an `install_skill` default of `allow_contract=False` so an untrusted bundle's `contract.py` is not executed. Remaining: true isolation (subprocess or container, capability scoping) for opted-in contracts. (ADR 0008)
- `BL-134` [resolved] [S] Secret and PII redaction for event content: `harness.Redactor` and `harness.RedactingSink`, scrubbing sensitive argument names, secret-shaped values, and over-long scalars before a sink. Closes plaintext leakage of tool arguments into sinks. (ADR 0008)
- `BL-150` [in-progress] [S] Pin GitHub Actions to commit SHAs and add a blocking dependency-audit gate. Delivered (ADR 0010): a blocking `dependency-audit` job (`pip-audit` over the exported `uv.lock`, wired into the `ci-success` aggregate) plus the `release` workflow's provenance attestation. Remaining: commit-SHA pinning of every GitHub Action, deferred not faked (the run environment cannot resolve third-party action SHAs; a fabricated 40-char hash is worse than an honest tag pin, so this is a maintainer/Dependabot action like `BL-162`). (ADR 0008, ADR 0010)

## AI quality and safety (Tier 1)

Practice gaps the analysis found that were not previously tracked.

- `BL-130` [resolved] [L] Agent evaluation harness plus a CI regression gate: golden `(query, expected skill)` sets with P@1 / MRR for dispatch, and a contract-outcome trajectory fixture. Delivered (ADR 0011): a top-level `evaluation/` component (`metrics`, `dataset`, `harness`), the in-tree `evaluation/data/skills_dispatch.json` golden set, `scripts/eval.py`, and a blocking CI `evaluation` job wired into the `ci-success` aggregate (mypy and the coverage target now include `evaluation`). `evaluate_dispatch` (P@1 / MRR) and `evaluate_trajectory` (expected vs actual contract terminal outcome) are deterministic and network-free; the harness also runs on an LLM dispatcher / live runtime for when BL-120 lands. Closes `LIMITATIONS.md` L6. (ADR 0008, ADR 0011)
- `BL-131` [resolved] [L] `SemanticMemoryStore` extension Protocol (vector write plus similarity query) beside `MemoryStore`, with one reference implementation; reuse the `EmbeddingProvider` from `BL-110`. Delivered (ADR 0011): the `SemanticMemoryStore` Protocol and `InMemorySemanticStore`, a deterministic in-tree reference that reuses the BL-110 `HashingEmbeddingProvider` through memory's own minimal `Embedder` Protocol (memory does not import skills; the layering stays one-way). Core ops delegate to `InMemoryStore` so namespace/TTL/audit are inherited; a deleted or expired key's vector is pruned. Closes the vector-retrieval half of `LIMITATIONS.md` L5 (compaction / tiering stays `BL-135`). Enables just-in-time retrieval in-tree (S2). (ADR 0004, ADR 0008, ADR 0011)
- `BL-132` [pending] [M] Prompt and response caching on the runtime adapter: cache-breakpoint control for the stable tools/system prefix and surfacing `cache_creation_input_tokens` / `cache_read_input_tokens` (S3). Pairs with cost accounting (`BL-123`). (ADR 0003, ADR 0008)
- `BL-137` [resolved] [M] Structured tool-error result for a soft governance reject, instead of returning the `[blocked: ...]` string as the tool's value. Delivered: `PydanticAIRuntime(soft_reject_as_error=True)` raises the framework's `ModelRetry` (a typed rejection the model handles as an error) instead of the string; default False preserves L1. Pairs with the guard fix (a SOFT governance predicate now actually reaches this path; ADR 0010 section 3). (ADR 0002, ADR 0008, ADR 0010)
- `BL-139` [resolved] [S] Documented prompt-injection posture: tool results, MCP output, and skill bodies are untrusted external content; state the handling and content-isolation expectations in `SECURITY.md` (S1, S2). Delivered in the ADR 0009 audit: `SECURITY.md` "Untrusted content and prompt injection" section. (ADR 0008, ADR 0009)

## Reliability and observability (Tier 2)

- `BL-135` [pending] [L] Memory compaction, summarisation, and tiering (hot to cold), and a size or LRU bound on the sweeper, not age only. Long-horizon workloads grow unbounded (S2: context compaction). (ADR 0004, ADR 0008)
- `BL-136` [resolved] [M] Retry, backoff, and circuit-breaker policy at the runtime boundary; the model previously had to re-issue a failed call. Delivered: an opt-in `harness.RetryPolicy` (bounded exponential backoff, a `retry_on` allowlist, a per-instance circuit breaker) wired into `PydanticAIRuntime.run`; a contract-terminal outcome (governance / approval / budget / cancellation) is never retried and retries share the budget. The memory-adapter-boundary breaker is the documented remainder. (ADR 0003, ADR 0008, ADR 0010)
- `BL-138` [pending] [M] OTel GenAI semantic conventions on the spans from `BL-113`: `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens` / `output_tokens` / `cache_read.input_tokens`, and `execute_tool` spans (S4). Refines `BL-113`. (ADR 0002, ADR 0008)

## Governance (Tier 3)

- `BL-152` [resolved] [M] Full REUSE / SPDX conversion with a CI check. Delivered: a tree-wide `REUSE.toml` (REUSE 3.x) plus `LICENSES/Apache-2.0.txt`, gated by `reuse lint` in CI (`reuse` 6.2.0 verified compliant, 177/177 files). A single `REUSE.toml` was chosen over a per-file header on ~120 files: spec-sanctioned, no diff churn, no line-citation breakage; an inline per-file `SPDX-License-Identifier` is still allowed via `precedence = "aggregate"`. (ADR 0008, ADR 0010)
- `BL-153` [resolved] [S] Governance documents: `STATUS.md`, `LIMITATIONS.md`, `CHANGELOG.md`, the ADR index (`docs/adr/README.md`), and an expanded `CONTRIBUTING.md` (DCO sign-off, SPDX baseline, security-review checkpoint, governance section). (ADR 0008)

## Release and operations (Tier 4)

- `BL-151` [resolved] [M] Versioning and release policy plus a release workflow and operational notes. Delivered: `docs/releasing.md` (semver-from-1.0 policy, the pre-1.0 `0.0.x` rule, release steps, rollback, per-backend memory backup/restore) and `.github/workflows/release.yml` (tag-triggered: full quality gate, `uv build`, CycloneDX SBOM, provenance attestation, GitHub Release). Publishing to an index is left a deliberate human gate pre-1.0. SLSA Build L2+ and signed-index publishing are the tracked remainder (`LIMITATIONS.md` L1/L4). (ADR 0008, ADR 0010)

## Code audit (ADR 0009, 2026-05-17)

A full in-depth audit of `harness`, `memory`, `skills`, `workloads`,
the CLI, and the documentation set. The clear correctness and security
bugs were fixed additively in the same increment (`BL-159`, `BL-160`);
the rest are tracked here and documented in `LIMITATIONS.md` (L10-L14).
ADR 0009 records the cross-cutting decisions. Same conventions and ID
discipline as above; `BL-1xx` range.

- `BL-154` [resolved] [M] Budgets do not accumulate across an approval pause and resume. Delivered: `ResumableState` carries `consumed_steps/tokens/tool_calls/per_tool/cost_usd`; `run_under_contract` stamps `BudgetTracker.snapshot()` onto the paused state and seeds the resumed tracker via new `initial_*` kwargs, so budgets accumulate across the pause. Scope: this stops the per-pause reset; eliminating the replay (so already-completed non-approval tool calls do not re-execute and re-charge) is the non-replay resume, still tracked as `BL-114` (`LIMITATIONS.md` L10 updated to reflect the partial). (ADR 0003, ADR 0009, ADR 0010)
- `BL-155` [pending] [L] True wall-clock preemption for a fully blocking, non-cooperative tool (thread/process execution so the watchdog can interrupt CPU-bound or sync-I/O tool code, not only await boundaries). (ADR 0003, ADR 0009; `LIMITATIONS.md` L11)
- `BL-156` [resolved] [M] `EncryptedStore` / `ACLStore` forward the extension Protocols of the store they wrap. Delivered: `memory.wrap_acl` / `memory.wrap_encrypted` factories compose a decorator subclass mixing in only the Protocols the inner store satisfies, so `isinstance` stays truthful (unconditional forwarding would fake a capability, the ADR 0004 "don't fake it" violation). `EncryptedStore` forwards Batch/Scan/ContentAddressable/Sweepable (seal/unseal as needed) but intentionally NOT CAS (GCM nonce randomisation makes ciphertext-equality CAS unrepresentable; a documented deviation, `memory/README.md`). The bare `EncryptedStore`/`ACLStore` constructors are unchanged. (ADR 0004, ADR 0009, ADR 0010; `LIMITATIONS.md` L12 updated)
- `BL-157` [resolved] [S] DynamoDB float-second expiry. Delivered: `_item` stores `exp` as `str(time.time()+ttl)` and the CAS `:now` is `str(time.time())` (float), so sub-second TTL holds and read vs `compare_and_set` agree at a second boundary; DynamoDB's own native TTL sweep reads the integer part and is best-effort anyway. (ADR 0004, ADR 0009, ADR 0010; `LIMITATIONS.md` L13 updated)
- `BL-158` [resolved] [S] Document the out-of-tree workload trust boundary (loading a path executes its Python, no skill-install-style gate). Delivered: `LIMITATIONS.md` L14, `SECURITY.md` scope and hardening posture, `workloads.load_workload_from_path` docstring. (ADR 0005, ADR 0009)
- `BL-159` [resolved] [M] Audit correctness/security fixes, additive: non-finite cosine similarity returns 0.0 instead of clamping NaN to 1.0 (`skills.embeddings`); `first_json_array` is single-pass linear, not O(n^2), against adversarial model output (`skills.dispatchers._json`); a JSON `bool` `confidence` is rejected, not coerced to 1.0 (LLM and skill-based dispatchers); `EncryptedStore` / `ACLStore` validate keys before any keyed operation per the `MemoryStore` Protocol (also closes an AAD cross-key collision); `Redactor` walks every event field, not only dict-valued ones. Regression tests added. (ADR 0009)
- `BL-160` [resolved] [S] Documentation-accuracy fixes (historical record of the ADR 0009 pass; the "eight" count it asserted was itself wrong and is corrected forward by ADR 0010 section 6 / `BL-163`+ context: seven *router* dispatchers plus the InstrumentedDispatcher wrapper and the `default_dispatcher` factory). README dispatcher count (eight, not "seven"); `docs/runtime-providers.md` stale line citations; `harness.redaction` BL reference (`BL-134`, was `BL-130`); `workloads.manifest` "Phase 5" present tense; the "five reference dispatchers" docstrings; the wall-clock watchdog "preemptive" wording; ADR 0005/0006 factual errata (the `skills/example/` path, the now-eight dispatchers, the L2-delivered "deferred" items) recorded in ADR 0009 (ADRs are immutable, so noted forward, not edited). (ADR 0009)
- `BL-161` [resolved] [M] Deferred audit hardening, by area. Delivered. Skill install: a non-file member inside the wanted subtree is rejected (not silently skipped), each member read is clamped to the remaining total budget, and `SkillRegistry.from_directory` takes an `allow_contract` passthrough. Memory: `SQLiteStore.mset`/`mdelete` are one `BEGIN IMMEDIATE` transaction; `S3Store.list_keys` pushes the prefix server-side; `S3Store.scan` and `DynamoDBStore.scan` loop non-terminal empty pages instead of falsely signalling exhaustion; `name@version` parses via `rpartition("@")`. CLI: `agents run` honours a model-free manifest dispatcher (keyword/embedding) and reports a missing-dependency `ImportError` cleanly like `workloads list`. (ADR 0002/0004/0005/0006, ADR 0009, ADR 0010)
- `BL-162` [resolved] [XS] Repoint `main` branch protection from the stale required context `test` (no job emits it since the 3.12/3.13 matrix split) to `ci-success`, the stable aggregate gate ADR 0008 section 4 added for exactly this. Optionally also require `lint`, `type-check`, `analyze (python)`. Repository Settings, not a file, so no PR can do it; until then every PR shows a perpetual "Expected, waiting for status to be reported". Resolution must repoint, not relax: keep required checks and no-bypass on; removing the check or allowing bypass is explicitly rejected (it would delete the gate, not fix it). Resolved 2026-05-17 by the maintainer: branch protection now requires `lint`, `type-check`, `ci-success` (stale `test` removed; required checks and no-bypass kept on). Settings change, so no commit artifact. (ADR 0008 section 4, ADR 0009 section 5)

## Code audit (ADR 0010, 2026-05-17)

A second in-depth audit during the L3 wave, by area, against the same
green gates. The clear bugs were fixed additively with regression tests
in the same increment; ADR 0010 section 3 is the why. Same conventions
and ID discipline; `BL-1xx` range.

- `BL-163` [resolved] [S] `HarnessToolGuard` returned APPROVE for a SOFT governance failure, so a soft governance predicate was a silent no-op and the runtime's documented soft-reject path was unreachable. Now returns REJECT/SOFT (the runtime logs-and-continues; pairs with `BL-137`). Existing test updated to the corrected contract. (ADR 0002, ADR 0010)
- `BL-164` [resolved] [S] A raising `RecoveryHandler` aborted the run, contradicting recovery.py's "a soft violation never halts". The handler is now contained: `RecoveryApplied(recovered=False, action="recovery handler raised: ...")` and the soft path continues. (ADR 0002, ADR 0010)
- `BL-165` [resolved] [S] `PydanticAIRuntime.run` caught `BaseException` and could reinterpret a wall-clock cancellation / `BudgetExceeded` as an approval pause when guard state was also set. It now re-raises `asyncio.CancelledError` / `BudgetExceeded` before consulting guard state. (ADR 0003, ADR 0010)
- `BL-166` [resolved] [S] `compose_contracts` kept the first occurrence on a predicate-name collision, which could keep a SOFT predicate over a HARD one and silently weaken a reviewed obligation. It now keeps the strictest (HARD over SOFT). (ADR 0002, ADR 0010)
- `BL-167` [resolved] [S] `MemoryAudit` rejected a missing base-event key but not a reserved one (`namespace`, `key`, `kind`, ...); a caller-supplied `namespace` would raise "multiple values" mid-run on the first emit. Reserved keys are now rejected at construction, like the missing-key check. (ADR 0004, ADR 0010)
- `BL-168` [resolved] [S] `SQLiteStore.sweep_expired` used `<=` while `read`/`list_keys`/`scan` use strict `>`, so an entry exactly at its expiry instant was live to readers but swept. The sweep boundary is now strict `<`, consistent with the readers. (ADR 0004, ADR 0010)
- `BL-169` [resolved] [S] `LocalSkillSource.fetch` used `shutil.copytree` (default `symlinks=False`), dereferencing symlinks, so a crafted local mirror could copy a host secret's contents into the installed bundle. It now copies regular files only and refuses a symlink anywhere in the subtree. (ADR 0006, ADR 0010)
- `BL-170` [resolved] [S] `S3Store.scan` could return `("", [])` (false exhaustion) for a page that was entirely expired-but-unswept while live keys remained behind a continuation token. It now pages internally until a live key is found or the listing truly ends (parity with the `DynamoDBStore.scan` fix in `BL-161`). (ADR 0004, ADR 0010)
- `BL-171` [pending] [M] Prompt and response caching on the runtime adapter (`BL-132`) is deferred from the ADR 0010 wave: a correct implementation needs a verified PydanticAI provider-cache API and a live model to validate (like `BL-120`); a no-op flag would breach the "no half-finished implementation" bar. Tracked as the continuation of `BL-132`. (ADR 0003, ADR 0010)

## Code audit (ADR 0011, 2026-05-17)

A third in-depth audit, by area, against the same green gates. The
clear bugs were fixed additively with regression tests in the same
increment; ADR 0011 section 1 is the why. Same conventions and ID
discipline; `BL-1xx` range. Resolution reference: ADR 0011 and branch
`claude/audit-and-docs-update-8ObHK` (stated here once).

- `BL-172` [resolved] [M] A pre-existing `dest/<name>` symlink let `GitHubSkillSource` / `MarketplaceSkillSource` escape the install directory: the code resolved the path before clearing it, so `resolve()` followed the link and extraction wrote members fully outside `dest` (the network-source twin of the `BL-169` `LocalSkillSource` hole, not propagated there). One hardened `_prepare_install_dir` unlinks the link itself before resolving and asserts containment. (ADR 0006, ADR 0008, ADR 0011)
- `BL-173` [resolved] [M] `_balanced_spans` recorded the matched substring on every nested `]`, so a nested `[[[...]]]` blob from an untrusted model/MCP tool was O(n^2) in time and memory (a decompression-bomb analogue; the `BL-159` rewrite removed the per-`[` restart but not the nested-slice blowup). It now records an O(1) `(open, close)` index pair per close and `first_json_array` slices at most a capped number of candidates lazily, in opening order, so a legitimate top-level array (always candidate one) is unaffected. `RecursionError` from a pathologically deep span is caught in the extractor and at the LLM / skill-based dispatcher boundary, preserving the DispatchError contract. (ADR 0006, ADR 0009, ADR 0011)
- `BL-174` [resolved] [S] `compose_contracts` governance was a first-occurrence union, so a workload's SOFT `delete_guard` declared before a skill's same-named HARD veto silently downgraded the veto to SOFT. Governance is the most safety-critical set; it now keeps the strictest instance, the governance analogue of the `BL-166` pre/inv/post fix. (ADR 0002, ADR 0011)
- `BL-175` [resolved] [S] A postcondition retry directive (`BL-102`) re-ran the postcondition loop and re-recorded every predicate into the `DriftMonitor`, inflating the JSD distribution. Postcondition drift is now recorded exactly once per run (the final, non-retried leg); a leg abandoned for a retry does not contribute. (ADR 0002, ADR 0011)
- `BL-176` [resolved] [XS] `run_under_contract` never set `parent_span_id`, so a contract run nested inside another emitted flat sibling spans. It gains an optional `parent_span_id` stamped onto every emitted event; None preserves the prior behaviour. (ADR 0002, ADR 0011)
- `BL-177` [resolved] [S] `DynamoDBStore.compare_and_set` (match branch) and `compare_and_delete` gated on `exp > :now` while `_live_item` treats a row as expired only when `now > exp` (live while `now <= exp`), so a row at the exact expiry instant was readable but CAS-absent. The conditions now use `exp >= :now`, the read-vs-CAS boundary class `BL-157` / `BL-168` fixed for the other paths. (ADR 0004, ADR 0011)
- `BL-178` [resolved] [XS] `SQLiteStore.mset` / `mdelete` of an empty batch still ran `BEGIN IMMEDIATE` ... `COMMIT`, taking the database write lock to do nothing; an empty batch is now an early no-op (no behaviour change: an empty batch already produced no rows and no audit). (ADR 0004, ADR 0011)
- `BL-179` [pending] [M] `RetryPolicy` charges token / step usage from the final attempt's `result.usage` only: PydanticAI raises without exposing partial usage on a failed `agent.run()`, so a retried run can exceed `max_tokens` / `max_steps` by the failed legs' usage (wall-clock is bounded end to end and tool-calls are fed live, so those dimensions still hold). The docstring is corrected; closing the gap needs upstream partial-usage on the exception path, the same upstream-dependent shape as `BL-114` / `BL-132`, so it is tracked, not faked. (ADR 0003, ADR 0011; `LIMITATIONS.md` L15)

## PR #28 review follow-ups (ADR 0011)

Findings from the Copilot / Codex automated reviews of the ADR 0011
branch, fixed additively on the same branch with regression tests.
Same conventions and ID discipline; resolution reference ADR 0011 and
branch `claude/audit-and-docs-update-8ObHK`.

- `BL-181` [resolved] [M] Adopting a `VersionedKeyProvider` on a store sealed by a plain `KeyProvider` made prior data unreadable (the versioned `_unseal` only parsed the envelope; the formats have no distinguishing marker). Delivered: an authenticated legacy fallback retries a non-envelope value as legacy `nonce+ct` with the current key; AES-GCM authentication guarantees no silent wrong value, and the original error surfaces if both interpretations fail. Migration contract documented (`LIMITATIONS.md` L16). (ADR 0004, ADR 0011)
- `BL-182` [resolved] [S] `first_json_array` (BL-173) capped candidate *count* (64) and `break` on the first over-budget span, so a valid array after many small leading bracket fragments, or after a larger one, was wrongly rejected. The bound is now on parse *work*: an O(1) length skip of an oversized span plus a cumulative parsed-byte budget, `continue` (not `break`) so every small span in opening order is still tried. Keeps the DoS bound (the O(n^2) was per-candidate parse, now bounded). (ADR 0006, ADR 0011)
- `BL-184` [resolved] [XS] Review-polish trio: the `_balanced_spans` docstring still named the removed `_MAX_CANDIDATES` (now `_MAX_CANDIDATE_BYTES` / `_MAX_TOTAL_PARSE_BYTES`); `_prepare_install_dir` ran `shutil.rmtree` on a pre-existing regular file at `dest/<name>` (NotADirectoryError, breaking its "clear" contract) and now unlinks a non-dir; `_decode_key` now wraps a bad base64/hex/utf-8 key as a clear `ValueError` naming the expected encoding (matching the other Env/File provider errors at the config trust boundary). (ADR 0006, ADR 0011)
- `BL-183` [resolved] [S] `evaluate_trajectory` (BL-130) scored an approval pause (a `ResumableState` return, no exception) as `completed`, inflating accuracy, and re-raised `ApprovalDenied` (aborting the whole run) because it was not in the outcome map. `TrajectoryOutcome` now includes `paused` and `approval_denied`, and both paths are classified. `wrap_acl` also now forwards `VersionedMemoryStore` (BL-156 truthful isinstance), and `InMemorySemanticStore.query_semantic` reads its vector via `.get()` so a concurrent write/delete cannot raise `KeyError`. (ADR 0002, ADR 0004, ADR 0006, ADR 0011)

## Cross-repo review: provenance + Anthropic capabilities (ADR 0012)

- `BL-185` [resolved] [M] A cross-repo review against the sibling `sentinel` corpus found this repo had no re-validatable record binding a completed run to the contract that enforced it. Delivered: `harness.RunRecord` (frozen, schema-versioned), `contract_digest` (SHA-256 of a contract's order-independent behavioural surface), `verify_run_record`, an opt-in `record_sink` keyword on `run_under_contract` emitting once at every terminal point, the gen-schema-guarded `docs/schema/run-record.json`, and the offline gate `scripts/check_run_records.py`. Two `sentinel` defects were cross-checked and deliberately not copied: its provenance hash is reconstructed from `git log` (re-stampable by a later fix PR) so the digest here is bound in-process at enforcement time instead; and its consistency CI routes findings to warnings yet exits 0, so every check here is a hard error with no warn-and-pass tier. Additive; the default reproduces prior control flow and exceptions byte-for-byte. (ADR 0007, ADR 0012)
- `BL-186` [resolved] [M] Optional Anthropic API capabilities a workload profits from that the single-run `Runtime` Protocol does not cover: `harness.AnthropicBatchProcessor` (Message Batches, async bulk at 50% token price; client injected for testability, lazy `from_env` raising a clear extra-naming error when absent; caller owns the wait loop so a harness can interleave budget/cancel checks) and `harness.cache_control_system` (prefix-stable prompt-cache block with the silent-invalidator constraint documented at the call site). Behind a new optional `anthropic` extra; the module imports and type-checks with the SDK absent (ADR 0007 idiom). Distinct from the deferred `BL-171`/`BL-132` runtime-adapter caching: this is a standalone helper plus a separate bulk surface, not a runtime-adapter flag. (ADR 0007, ADR 0012)

- `BL-187` [resolved] [S] OpenAI counterpart of `BL-186`, folded into the same PR: `harness.OpenAIBatchProcessor` wraps the OpenAI Batch API (JSONL input-file upload, batch create, JSONL output/error-file decode) behind an optional `openai` extra, same injected-client + lazy `from_env` + caller-owned-wait-loop design. Not a copy of the Anthropic wrapper (different API shape; no `cache_control` analogue since OpenAI caching is automatic). `OpenAIBatchRequest.model` is required with no default, because current OpenAI model ids cannot be verified against a trusted source here and a guessed id would be a silent mis-identification. Model-level OpenAI already works via the provider-neutral `Runtime`. (ADR 0007, ADR 0012)

## Code audit (ADR 0013, 2026-05-19)

A fifth in-depth audit, by area, against the same green gates, focused
on the *classes* the prior audits fixed pointwise and the paths the
recent major dependency bumps (`anthropic`, `openai`, `redis`)
exercise. The clear bugs were fixed additively with regression tests in
the same increment; ADR 0013 is the why. Same conventions and ID
discipline; `BL-1xx` range. Resolution reference: ADR 0013 and branch
`claude/code-audit-improvements-3xpej` (stated here once).

- `BL-188` [resolved] [S] `InMemoryStore` / `SQLiteStore` `list_keys` and `scan` used `expires_at > now` while `read` / `mget` / CAS / `read_versioned` / `sweep_expired` treat an entry live until `now > expires_at` (live at the exact expiry instant). A key that `read` still returned, and that `sweep_expired` still kept, was absent from `list_keys`/`scan` for one tick: the read-vs-CAS boundary class (BL-157/168/177) unfixed for the *listing* paths of the two in-tree reference adapters (BL-168's own fix comment wrongly asserted list/scan already agreed). Both paths now use the `now <= expires_at` live boundary; the misleading comment is corrected. DynamoDB/S3 verified consistent, unchanged. (ADR 0004, ADR 0013)
- `BL-189` [resolved] [S] `OpenAIBatchProcessor.results` yielded `error_type="http_None"` for an output-file row with `response: null` and a structured `error` (a request-level failure OpenAI writes to the *output* file, distinct from the error-file rows), discarding `line["error"]` on a billing-relevant bulk path. The non-200 branch now prefers `error.code` when present, falling back to `http_<status>` only with no error object. Additive; 200 rows and error-file rows unchanged. (ADR 0007, ADR 0012, ADR 0013)
- `BL-190` [resolved] [XS] `LocalSkillSource.fetch` cleared `dest/<name>` with a bare `shutil.rmtree`; on a pre-existing `dest/<name>` symlink that raises an unhandled `OSError` instead of `SkillLoadError` and leaves the link in place. Not an escape (`shutil.rmtree` refuses the link; the post-copy containment check holds), so a robustness / clean-error / defence-in-depth consistency fix: BL-172 named `LocalSkillSource` the "twin" and built `_prepare_install_dir` but only routed the network sources through it. `LocalSkillSource` now uses the same audited symlink-safe clear; one clear for all sources. (ADR 0006, ADR 0011, ADR 0013)
- `BL-191` [resolved] [S] `_balanced_spans` materialised one `(open, end)` int-pair per closing bracket *before* `first_json_array` ran, so a bracket-heavy untrusted body (`"[]" * n`) cost an O(n) span list (~120 B/pair, ~30x amplification over the source) regardless of the BL-173/182 parse-work budget, and the eager list defeated the fast first-candidate return. A new memory axis the count-vs-work reasoning did not cover. A hard `_MAX_SPANS` ceiling (65536, far above any legitimate dispatch response) now bounds the list; overflow degrades to the existing malformed-input / DispatchError contract (the oversized-span / RecursionError posture). (ADR 0006, ADR 0009, ADR 0013)
- `BL-192` [resolved] [XS] `scripts/check_run_records.py` validated the `--registry` payload was a JSON object but never its values; a non-canonical registry (a JSON number, explicit `null`, uppercase hex) can never equal a model-normalised `contract_digest`, so the gate was silently unsatisfiable and reported a nonsensical "does not match the registry digest 123" per-record message. It already failed closed (no forgery bypass) but unactionably. Registry values are now validated as canonical lowercase 64-hex at load (mirroring the per-record model strictness); a malformed registry is the documented invocation failure (exit 2) naming the offending keys. (ADR 0012, ADR 0013)

## ADR 0013 follow-up: approval-resume argument binding (2026-05-20)

A single post-audit security fix in PR #46 (`a511760`), discovered after
ADR 0013 landed. The default `HarnessToolGuard` mints a fresh
`interruption_id` per check, so the id is not a stable cross-pause
handle; binding by `tool` alone let a resolved approval for one set of
arguments satisfy a *different* call to the same tool on resume. The
fix tightens the binding to the full `(tool, arguments)` tuple, with a
regression test (`test_gate_resume_does_not_reuse_stale_approval_for_new_arguments`).
Same conventions and ID discipline; `BL-1xx` range.

- `BL-193` [resolved] [S] On resume, `_resolved_decision` matched a pending approval by `tool` only, so a stale approval for `risky({"path": "approved.txt"})` satisfied a new `risky({"path": "victim.txt"})` call (the default `HarnessToolGuard` issues a fresh `interruption_id` per check, so the id alone is not a stable cross-pause handle). The lookup now binds by the full `(tool, arguments)` tuple, mirroring `ApprovalInterruption.arguments`; the test `tests/harness/test_runtime_adapter.py::test_gate_resume_does_not_reuse_stale_approval_for_new_arguments` exercises the boundary. Authorization-boundary fix (`SECURITY.md` "Untrusted content and prompt injection"); additive to the L1 Protocols (a strictly narrower match condition; the prior behaviour was incorrectly accepting). (ADR 0002, ADR 0003, ADR 0007, ADR 0013)

## Dependency-audit gate hardening (2026-05-20)

A CI-policy fix folded into PR #47: the `dependency-audit` job started
failing on every run (including `main`) once `uvx pip-audit` resolved
its dry-run env to Python 3.11 by default. Same conventions and ID
discipline; `BL-1xx` range.

- `BL-194` [resolved] [XS] The `dependency-audit` job in `.github/workflows/ci.yml` invoked `uvx pip-audit --strict ... -r audit-requirements.txt`. uvx picks Python 3.11 unless pinned, so the marker `python_version < "3.12"` on `backports.tarfile` (a transitive of `jaraco-context==6.1.2`, in turn pulled by the `keyring` chain) became true and demanded an unpinned `>=1.1.1` install that pip refuses under `--require-hashes`. The audit env now matches the project (`uvx --python 3.12 pip-audit ...`), so the marker is false and the gate runs to completion. The same step also ignores `PYSEC-2025-183` (CVE-2025-45768), a maintainer-disputed advisory against `pyjwt` (the maintainer rejects it as an application-level concern: pyjwt accepts a short symmetric key, but the application chooses the key length; no fix version is published). `pyjwt` is only reachable here as `mcp` -> `pydantic-ai-slim` -> `pydantic-ai` transitively; no JWT path is exercised. The CI step carries the rationale and a revisit trigger (withdrawal, replacement advisory, or a hardened pyjwt default). (ADR 0008, ADR 0010)

## Sources consulted

Primary sources cross-checked before the `BL-130`-`BL-153` edits.
Accessed 2026-05-17.

- S1 Anthropic, "Building effective agents". https://www.anthropic.com/engineering/building-effective-agents
- S2 Anthropic, "Effective context engineering for AI agents". https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- S3 Anthropic, "Prompt caching". https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- S4 OpenTelemetry, "Semantic conventions for generative AI spans". https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/
- S5 SLSA v1.0, "Build levels". https://slsa.dev/spec/v1.0/levels
- S6 Python documentation, "tarfile extraction filters" (PEP 706). https://docs.python.org/3/library/tarfile.html
- S7 Agent Skills specification (the existing compliance baseline, referenced in `skills/types.py`). https://agentskills.io/specification

## Resolved by later phases

The ADR-0002/0005 deferrals are now fully addressed by the L2 wave
(ADR 0007), not just partially:

- `ADR-0002` "Live governance enforcement": surface (Phase 2) + runtime
  wiring (`BL-001`, `BL-003`) both shipped. Governance now fires for
  local **and** MCP tool calls. Resolved.
- `ADR-0002` "Live approval interruption": surface + adapter wiring
  (`BL-002`) shipped: pause -> `ResumableState` -> resume. Resolved.
- `ADR-0002` recovery (R in P,I,G,R) and JSD drift: `BL-061`, `BL-062`.
  Resolved.
- `ADR-0005` "Skill resolution": `SkillRegistry` (Phase 5) + the
  `BL-011` validator (version-aware) shipped. Resolved.

## Suggested first-week sequence (completed)

This ordering was the recommended entry path; the whole backlog was
delivered, so it is retained only as historical context.

1. `BL-010` (XS): workload name-matches-directory validator. Done.
2. `BL-011` (S): workload skills resolution validator. Done.
3. `BL-001` (S): wire `HarnessToolGuard` into the PydanticAI adapter. Done.
4. `BL-040` (S): memory operation events through `EventSink`. Done.
5. `BL-020` (S): `agents workloads list`. Done.

The L1 framework moved from "scaffolded" to "wired through end to end",
plus production memory backends, observability, composition, and
hardening.

## Maintenance

- When an item is started, change `[pending]` to `[in-progress]` and add the branch name.
- When merged, change to `[resolved]` and add the merge commit.
- New L2 items discovered after Phase 5 are added with the next free ID per section and dated.
- L3 items use the `BL-1xx` range, are sourced from ADR 0007 revisit triggers and in-code deferrals, and are dated when added. The same start/merge status transitions apply.
