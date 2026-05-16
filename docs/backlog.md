# L2 Backlog

Consolidated from ADRs 0002 through 0006. Generated 2026-05-16 after Phase 5 (`7c26543`).

## Conventions

Each item has an ID, status, size estimate, source ADR, and notes.

- Status: `pending`, `in-progress`, `blocked`, `resolved`.
- Size: `XS` (~30 min), `S` (~1 to 2 h), `M` (~half day), `L` (~day or more).
- IDs are stable; do not renumber on removal. Use `resolved` instead of deleting.

## Adapter integration (highest priority: unblocks real workloads)

`HarnessToolGuard` and `BudgetTracker` ship in Phase 2 with full surfaces and event emission, but neither is wired into the default `PydanticAI` adapter's tool-call path. Tool calls bypass both at runtime today. Closing this is the L1-to-L2 bridge that any real workload needs.

- `BL-001` [in-progress] [S] Wire `HarnessToolGuard` into the PydanticAI adapter so tool calls hit `guard.check(tool, arguments)` before execution and respect `REJECT`, `REQUIRE_APPROVAL`, and `APPROVE` decisions. (ADR 0002, ADR 0003) — branch `claude/implement-l2-feature-5JpJX`
- `BL-002` [in-progress] [M] Live interruption-resume mid-run in the PydanticAI adapter: an `ApprovalInterruption` should pause the run, surface `ResumableState`, and resume cleanly on `.approve()` or `.deny()`. Lands with the first workload that needs human-in-the-loop approval. (ADR 0003) — branch `claude/implement-l2-feature-5JpJX`
- `BL-003` [in-progress] [S] Background watchdog for wall-clock budget enforcement. Currently `BudgetTracker` checks at step boundaries; long-running tools can exceed `max_wall_clock_seconds` without preemption. (ADR 0003) — branch `claude/implement-l2-feature-5JpJX`
- `BL-004` [in-progress] [S] Streaming budget enforcement: accumulate token usage during a stream and raise `BudgetExceeded` on threshold cross. (ADR 0003) — branch `claude/implement-l2-feature-5JpJX`

## Workload + skill validators

Now that Phase 5 ships `SkillRegistry`, several Phase 4 validators are unblocked.

- `BL-010` [in-progress] [XS] Workload loader: validate that `manifest.name` matches the package directory name. Silent mismatch today. (ADR 0005) — branch `claude/implement-l2-feature-5JpJX`
- `BL-011` [in-progress] [S] Workload loader: validate that every `skills:` entry resolves in a `SkillRegistry`. Optional dependency: the registry must be passed at load time. (ADR 0005, ADR 0006) — branch `claude/implement-l2-feature-5JpJX`
- `BL-012` [in-progress] [S] Validate skill `allowed-tools` entries against the harness's known tool catalog. (ADR 0006) — branch `claude/implement-l2-feature-5JpJX`
- `BL-013` [in-progress] [S] Manifest JSON Schema generation (`WorkloadManifest.model_json_schema()`) emitted to `docs/schema/workload-manifest.json` for editor autocomplete. (ADR 0005) — branch `claude/implement-l2-feature-5JpJX`

## CLI surface

- `BL-020` [pending] [S] `python -m agents workloads list` -> prints every loadable workload's name, version, description. (ADR 0005)
- `BL-021` [pending] [M] `python -m agents run <workload> <query>` -> loads the workload, dispatches via its configured `Dispatcher`, runs under contract, prints structured result. (ADR 0006)
- `BL-022` [pending] [S] `python -m agents skills list` -> prints every skill in `skills/`, grouped by lane.

## Memory adapters

`InMemoryStore` is the only `MemoryStore` adapter today. Production deployments need durable backends.

- `BL-030` [pending] [M] Redis adapter. Pipelining for batch ops, Lua scripts for atomic CAS, native TTL via `EXPIRE`. (ADR 0004)
- `BL-031` [pending] [M] SQLite adapter for durable single-host workloads. WAL mode, per-namespace tables. (ADR 0004)
- `BL-032` [pending] [M] S3 adapter for blobs and audit packs. Eventually-consistent; document the semantics deviation. (ADR 0004)
- `BL-033` [pending] [L] DynamoDB adapter for AWS-native deployments. Strongly-consistent reads optional. (ADR 0004)

## Observability

- `BL-040` [in-progress] [S] Memory operation events (`MemoryRead`, `MemoryWrite`, `MemoryDelete`) emitted through `EventSink`. Surface in `harness.events` is ready; the `MemoryStore` Protocol needs an optional `sink` parameter. (ADR 0004) — branch `claude/implement-l2-feature-5JpJX`
- `BL-041` [pending] [S] OTel-Collector-compatible `EventSink` implementation. `HarnessEvent` already carries `trace_id`, `span_id`, `parent_span_id`. (ADR 0002)
- `BL-042` [pending] [M] Dispatch performance instrumentation: per-dispatcher latency histograms, runtime token consumption, threshold-fallback rate. Feeds Grafana via OTel. (ADR 0006)

## Skill ecosystem

- `BL-050` [pending] [S] `MultiDispatcher` ensemble that combines results from several dispatchers via vote, average, or weighted blend. (ADR 0006)
- `BL-051` [pending] [M] Embedding-based dispatcher. Vector similarity between query and skill descriptions. Requires an embedding adapter or a Runtime that exposes embeddings. (ADR 0006)
- `BL-052` [pending] [M] Skill-level contracts (`skills/<name>/contract.py`) that compose with the workload contract. Composition rule: intersection of predicate sets. (ADR 0006)
- `BL-053` [pending] [M] Skill versioning and rollback. Track multiple versions of the same skill; load by `name@version`. (ADR 0006)
- `BL-054` [pending] [L] Skill installation from registries: `anthropics/skills` on GitHub, Vercel `skills.sh` marketplace. (ADR 0006)

## Composition (Bhardwaj agent-contract tuple)

- `BL-060` [pending] [M] Workload + skill contract composition. Intersection of predicate sets, governance union, approval-required union. (ADR 0002)
- `BL-061` [pending] [M] Recovery handlers for soft violations: the R in the Bhardwaj tuple. Predicates today flag-and-emit; recovery actions are unspecified. (ADR 0002)
- `BL-062` [pending] [L] JSD distributional drift instrumentation across runs. Aggregated state distribution per predicate. (ADR 0002)

## Production hardening

- `BL-070` [pending] [M] Encryption at rest for memory adapters. Per-adapter concern; the framework should provide a `KeyProvider` Protocol. (ADR 0004)
- `BL-071` [pending] [M] ACL / role-based per-key access controls on `MemoryStore`. The contract layer covers workload-boundary auth; per-key ACLs are an L2 refinement. (ADR 0004)
- `BL-072` [in-progress] [L] CAS / MVCC primitives in adapters that support them. Exposed via a separate `CASMemoryStore` Protocol so non-CAS backends do not have to fake it. Protocol + InMemoryStore reference impl landed; per-adapter impls land with each adapter. (ADR 0004) — branch `claude/implement-l2-feature-5JpJX`
- `BL-073` [in-progress] [S] Per-tool quotas (e.g. up to 3 calls to `search`, up to 1 call to `delete`). Currently a single `max_tool_calls` counter applies to all. (ADR 0003) — branch `claude/implement-l2-feature-5JpJX`

## Memory convenience

- `BL-080` [in-progress] [S] Active TTL sweep background task for `MemoryStore` adapters that benefit from it. `InMemoryStore` uses lazy expiry today. (ADR 0004) — branch `claude/implement-l2-feature-5JpJX`
- `BL-081` [in-progress] [S] Multi-key batch operations: `mget(keys)`, `mset(items)`, `mdelete(keys)`. (ADR 0004) — branch `claude/implement-l2-feature-5JpJX`
- `BL-082` [in-progress] [M] Iterator-style `list_keys` for very large keyspaces. Cursor-based; bounded result pages. (ADR 0004) — branch `claude/implement-l2-feature-5JpJX`
- `BL-083` [in-progress] [S] Content addressing: `write_content(value) -> sha256-hex-key`. Useful for immutable storage patterns. (ADR 0004) — branch `claude/implement-l2-feature-5JpJX`

## Workload convenience

- `BL-090` [pending] [M] Out-of-tree workloads. Load from arbitrary filesystem paths or installed packages, not just the `workloads/` package tree. (ADR 0005)

## Resolved by later phases

- `ADR-0002` "Live governance enforcement... Phase 2": Phase 2 shipped the surface (`ToolGuard`, `HarnessToolGuard`, `GovernanceViolated` event). Runtime wiring is the residual work in `BL-001` / `BL-003`. Mark as `partially resolved`.
- `ADR-0002` "Live approval interruption... Phase 2": same as above; surface complete, adapter wiring is `BL-002`. Mark as `partially resolved`.
- `ADR-0005` "Skill resolution... lands with Phase 5": Phase 5 shipped `SkillRegistry`. Validation itself is `BL-011`. Mark as `unblocked`.

## Suggested first-week sequence

If you start picking off items, this ordering minimizes review surface and resolves the highest-leverage gaps first.

1. `BL-010` (XS): workload name-matches-directory validator. Quick safety win.
2. `BL-011` (S): workload skills resolution validator. Cheap follow-on, now unblocked.
3. `BL-001` (S): wire `HarnessToolGuard` into the PydanticAI adapter. Real workloads need this.
4. `BL-040` (S): memory operation events through `EventSink`. Closes the L1 audit story end-to-end.
5. `BL-020` (S): `agents workloads list`. Low-cost discoverability win.

Roughly a half-day of work and the L1 framework moves from "scaffolded" to "wired through end to end."

## Maintenance

- When an item is started, change `[pending]` to `[in-progress]` and add the branch name.
- When merged, change to `[resolved]` and add the merge commit.
- New L2 items discovered after Phase 5 are added with the next free ID per section and dated.
