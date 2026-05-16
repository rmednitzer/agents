# L2 Backlog

Consolidated from ADRs 0002 through 0006. Generated 2026-05-16 after Phase 5 (`7c26543`). Updated 2026-05-16 after PR #20 merged to `main`.

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
- `BL-054` [resolved] [L] Skill installation from registries: `anthropics/skills` on GitHub, Vercel `skills.sh` marketplace. (ADR 0006)

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

- `BL-090` [resolved] [M] Out-of-tree workloads. Load from arbitrary filesystem paths or installed packages, not just the `workloads/` package tree. (ADR 0005)

# L3 backlog

Added 2026-05-16. Consolidated from ADR 0007's revisit triggers and the
deferrals recorded in L2 code/docstrings. Status: all `pending` (not
started). Same conventions as above; IDs use the `BL-1xx` range so they
do not collide with L2 (`BL-0xx`).

L3 is not "more breadth": L2 shipped the primitives, L3 mostly wires
them into the default execution path and supplies real implementations
behind the pluggable Protocols. The highest-leverage cluster is
"default-path wiring + one real workload".

## Default-path wiring

The L2 primitives exist but are opt-in / manually engaged. L3 makes the
framework use them by default.

- `BL-100` [pending] [M] Auto-compose a workload's loaded skill contracts with its workload contract in `run_under_contract`. `compose_contracts` and `Skill.contract()` exist (BL-052, BL-060) but composition is caller-driven today. (ADR 0002, ADR 0006)
- `BL-101` [pending] [M] Record predicate pass/fail into a `DriftMonitor` from the enforcement loop and emit a threshold-crossing event. `DriftMonitor` (BL-062) is standalone; nothing feeds it. (ADR 0002)
- `BL-102` [pending] [M] Recovery control flow: let a `RecoveryHandler` outcome drive retry / substitute / escalate, not just emit-and-continue. Today recovery (BL-061) is observational; the soft path always continues unchanged. (ADR 0002)
- `BL-103` [pending] [S] Fold `InstrumentedDispatcher` into the recommended default dispatcher composition and ship a worked OTel/Grafana wiring example. (ADR 0006, BL-042/050)
- `BL-104` [pending] [S] Harness/workload-runner-managed `TTLSweeper` lifecycle (start/stop tied to a run), instead of fully manual opt-in. (ADR 0004, BL-080)

## Real implementations behind the pluggable Protocols

L2 shipped Protocols plus deterministic test doubles; L3 supplies
production implementations.

- `BL-110` [pending] [M] A concrete `EmbeddingProvider` (via a Runtime or a provider SDK) so `EmbeddingDispatcher` (BL-051) is usable without a hand-rolled provider. (ADR 0006)
- `BL-111` [pending] [M] `KeyProvider` beyond `StaticKeyProvider`: env/file and a KMS-backed provider, plus key rotation/versioning for `EncryptedStore` (BL-070). (ADR 0004)
- `BL-112` [pending] [M] Marketplace `SkillSource` (Vercel `skills.sh`) and checksum/signature verification on install. Extends BL-054. (ADR 0006)
- `BL-113` [pending] [L] True OTel spans + trace-context propagation. `OTelSink` (BL-041) emits log records with trace/span as attributes because the OTel logs SDK is unstable; revisit when it stabilizes, add GenAI semantic conventions for streaming. (ADR 0002)
- `BL-114` [pending] [L] Deeper PydanticAI resume via `DeferredToolRequests` / `message_history` instead of re-running the agent on resume. `_resumable` (BL-002) replays today; revisit when PydanticAI's pause/resume primitive is stable. (ADR 0003 revisit trigger)

## Reference workload and loose ends

- `BL-120` [pending] [L] A real reference workload exercising the wired runtime end-to-end against a live model (only `_example` stub exists). Becomes the adapter's CI smoke, gated to skip without API keys.
- `BL-121` [pending] [S] Out-of-tree workloads from an installed package / `[project.entry-points]`, not just a filesystem path. Extends BL-090. (ADR 0005 revisit trigger)
- `BL-122` [pending] [S] Attribute-based / dynamic `AccessPolicy`, and an `AccessDenied` audit event through `EventSink`. Extends BL-071. (ADR 0004)
- `BL-123` [pending] [M] Cost and per-tool wall-clock / token budgets; today the per-tool cap (BL-073) is call-count only. (ADR 0003 revisit trigger)
- `BL-124` [pending] [L] MVCC / version tokens beyond compare-and-set, and multi-key transactions where the backend supports them. Extends BL-072. (ADR 0004)
- `BL-125` [pending] [S] `agents run` accepts typed input models + `--json` / streaming output, and an `agents skills install <source>` subcommand. Extends BL-021, BL-054. (ADR 0006)

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
