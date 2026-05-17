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

- Tier 0, security: `BL-112`, `BL-133`, `BL-134`, `BL-150`. The
  first increment landed (`BL-134` resolved; `BL-112`, `BL-133`
  in-progress; see "Security hardening").
- Tier 1, AI quality and safety: `BL-130`, `BL-131`, `BL-132`,
  `BL-137`, `BL-139`, plus `BL-110`, `BL-120`.
- Tier 2, reliability and observability: `BL-135`, `BL-136`, `BL-138`,
  plus `BL-100`-`BL-104`, `BL-113`, `BL-123`.
- Tier 3, governance: `BL-152`, `BL-153` (`BL-153` resolved).
- Tier 4, release and operations: `BL-151`.

Highest leverage remains "default-path wiring plus one real workload"
(`BL-100`, `BL-120`), now preceded by Tier 0 security.

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

## Security hardening (Tier 0)

First increment delivered 2026-05-17 (ADR 0008). The gate is defence in
depth, not a sandbox (`LIMITATIONS.md` L3).

- `BL-112` [in-progress] [M] Marketplace `SkillSource` (Vercel `skills.sh`) and integrity verification on install. Delivered: bounded download / member-count / per-member / total-size caps and an optional `sha256` on `GitHubSkillSource` (closes the decompression-bomb and unbounded-read exposure; cross-checked against S6). Remaining: a marketplace source and signature (not just checksum) verification. Extends `BL-054`. (ADR 0006, ADR 0008)
- `BL-133` [in-progress] [M] Skill execution isolation. Delivered: `discover_skill(allow_contract=...)` and an `install_skill` default of `allow_contract=False` so an untrusted bundle's `contract.py` is not executed. Remaining: true isolation (subprocess or container, capability scoping) for opted-in contracts. (ADR 0008)
- `BL-134` [resolved] [S] Secret and PII redaction for event content: `harness.Redactor` and `harness.RedactingSink`, scrubbing sensitive argument names, secret-shaped values, and over-long scalars before a sink. Closes plaintext leakage of tool arguments into sinks. (ADR 0008)
- `BL-150` [pending] [S] Pin GitHub Actions to commit SHAs and add a blocking dependency-audit gate (Dependabot proposes updates but is not a gate). Targets SLSA Build L1 provenance as a follow-on (S5). (ADR 0008)

## AI quality and safety (Tier 1)

Practice gaps the analysis found that were not previously tracked.

- `BL-130` [pending] [L] Agent evaluation harness plus a CI regression gate: golden `(query, expected skill)` sets with P@1 / MRR for dispatch, and a contract-outcome trajectory fixture. CI gates lint/types/coverage but not behaviour; routing quality can regress silently (S1: measure against clear success criteria). (ADR 0008)
- `BL-131` [pending] [L] `SemanticMemoryStore` extension Protocol (vector write plus similarity query) beside `MemoryStore`, with one reference implementation; reuse the `EmbeddingProvider` from `BL-110`. Enables just-in-time retrieval in-tree (S2). (ADR 0004, ADR 0008)
- `BL-132` [pending] [M] Prompt and response caching on the runtime adapter: cache-breakpoint control for the stable tools/system prefix and surfacing `cache_creation_input_tokens` / `cache_read_input_tokens` (S3). Pairs with cost accounting (`BL-123`). (ADR 0003, ADR 0008)
- `BL-137` [pending] [M] Structured tool-error result for a soft governance reject, instead of returning the `[blocked: ...]` string as the tool's value, so the model receives a typed rejection rather than apparent tool output (S1: clear agent-computer interface). (ADR 0002, ADR 0008)
- `BL-139` [pending] [S] Documented prompt-injection posture: tool results, MCP output, and skill bodies are untrusted external content; state the handling and content-isolation expectations in `SECURITY.md` (S1, S2). (ADR 0008)

## Reliability and observability (Tier 2)

- `BL-135` [pending] [L] Memory compaction, summarisation, and tiering (hot to cold), and a size or LRU bound on the sweeper, not age only. Long-horizon workloads grow unbounded (S2: context compaction). (ADR 0004, ADR 0008)
- `BL-136` [pending] [M] Retry, backoff, and circuit-breaker policy at the guard/runtime and memory-adapter boundary; today the model must re-issue a failed call. (ADR 0003, ADR 0008)
- `BL-138` [pending] [M] OTel GenAI semantic conventions on the spans from `BL-113`: `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens` / `output_tokens` / `cache_read.input_tokens`, and `execute_tool` spans (S4). Refines `BL-113`. (ADR 0002, ADR 0008)

## Governance (Tier 3)

- `BL-152` [pending] [M] Full REUSE / SPDX conversion: per-file `SPDX-License-Identifier` headers and a `REUSE.toml`, with a CI check. (ADR 0008)
- `BL-153` [resolved] [S] Governance documents: `STATUS.md`, `LIMITATIONS.md`, `CHANGELOG.md`, the ADR index (`docs/adr/README.md`), and an expanded `CONTRIBUTING.md` (DCO sign-off, SPDX baseline, security-review checkpoint, governance section). (ADR 0008)

## Release and operations (Tier 4)

- `BL-151` [pending] [M] Versioning and release policy plus a release workflow (signed artifacts, SBOM, build provenance) and operational notes (deploy, rollback, backup and restore for memory backends). Pre-1.0 today with no release lifecycle (`STATUS.md`, `LIMITATIONS.md` L1, L4; S5 for provenance). (ADR 0008)

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
