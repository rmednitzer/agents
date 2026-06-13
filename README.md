# Agents

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/rmednitzer/agents)

Modular execution substrate for governed agentic workloads: enforces behavioral contracts and resource budgets between untrusted model outputs and system capabilities, with pluggable memory backends and portable skill bundles.

## Status

L1 framework, the full L2 implementation wave, the L3 default-path
wiring + audit wave, the third-audit + L3 capability wave, the
run-provenance + provider-batch wave, the fifth-code-audit
hardening (plus the post-audit approval-resume binding fix, `BL-193`),
the BL-180 durable-adapter MVCC + transactional Protocol wave, the
sixth-code-audit hardening (`BL-197`-`208` plus the ADR 0015
deferred close `BL-209`-`211`), the BL-133 skill execution
isolation Protocol + subprocess reference, the `BL-212`-`BL-214` / `BL-224` / `BL-225` sweeper
size-bound wave (`BoundedSweepableStore` extension Protocol with
in-memory, SQLite, and opt-in Redis / DynamoDB / S3 references), the
seventh-code-audit hardening (`BL-215`-`BL-218`), the
eighth-code-audit hardening (`BL-219`-`BL-222`), the
ninth-code-audit hardening (`BL-223`), the tenth-code-audit
hardening (`BL-226` / `BL-227`, against the just-merged
`BoundedS3Store`), the eleventh-code-audit hardening
(`BL-228` / `BL-229`, closing the two open ADR 0020 revisit
triggers), the twelfth-code-audit hardening
(`BL-231` / `BL-232`, non-finite numeric configuration
validation), the thirteenth-code-audit hardening
(`BL-233`, sweep per-item DELETE containment on the network
adapters), the compaction / summarisation / tiering wave
(ADR 0024, `BL-234` / `BL-235`, closing `BL-135`), and the
fourteenth-audit process hardening (ADR 0025, `BL-236`-`BL-239`:
stale pip-audit suppression removed, lockfile-freshness CI gate,
unused `logfire` declaration dropped, comment-accuracy fixes), and
prompt caching on the runtime adapter (ADR 0026, `BL-132` /
`BL-171`: opt-in `model_settings` pass-through + cache-token
surfacing, live cache-hit validation coupled to `BL-120`), and the
deferred (non-replay) approval resume (ADR 0027, `BL-114`: opt-in
`approval_mode="deferred"`, the paused leg's history travels in
`ResumableState.runtime_state` and prior tool calls run exactly
once), the Vertex MCP analysis capability wave (ADR 0028-0038,
`BL-242`-`BL-253`: hybrid retrieval fusion + decay-ranked demotion,
graduated authority tiers, DEGRADED disposition + grounding, the
approval-context payload, the bitemporal fact store, two-step
parameter restatement, the operational-memory journal, the
graceful-degradation fallback chain, read-side freshness, session
rehydration, and the evidence-capture hook), and the
fifteenth-code-audit hardening (ADR 0039, `BL-254`-`BL-263`:
`LocalSkillSource` hardlink refusal, two run-time non-finite
guards, the `fuse_rrf` distinct-rank fix, the SQLite TTL-clock
ordering fix, the S3 list-vs-HEAD read-path fix, and the
`BoundedRedisStore.write` atomicity fix) on `main` (see
[docs/backlog.md](./docs/backlog.md),
[ADR 0007](./docs/adr/0007-l2-implementation-wave.md),
[ADR 0010](./docs/adr/0010-l3-default-path-wiring-and-audit-wave.md),
[ADR 0011](./docs/adr/0011-third-audit-and-l3-capability-wave.md),
[ADR 0012](./docs/adr/0012-run-provenance-and-anthropic-capabilities.md),
[ADR 0013](./docs/adr/0013-fifth-code-audit.md),
[ADR 0014](./docs/adr/0014-versioned-and-transactional-on-durable-adapters.md),
[ADR 0015](./docs/adr/0015-sixth-code-audit.md),
[ADR 0016](./docs/adr/0016-skill-execution-isolation.md),
[ADR 0017](./docs/adr/0017-seventh-code-audit.md),
[ADR 0018](./docs/adr/0018-eighth-code-audit.md),
[ADR 0019](./docs/adr/0019-ninth-code-audit.md),
[ADR 0020](./docs/adr/0020-tenth-code-audit.md),
[ADR 0021](./docs/adr/0021-eleventh-code-audit.md),
[ADR 0022](./docs/adr/0022-twelfth-code-audit.md),
[ADR 0023](./docs/adr/0023-thirteenth-code-audit.md),
[ADR 0024](./docs/adr/0024-compaction-summarisation-and-tiering.md),
[ADR 0025](./docs/adr/0025-fourteenth-audit-full-pass.md),
[ADR 0026](./docs/adr/0026-prompt-caching-on-the-runtime-adapter.md),
[ADR 0027](./docs/adr/0027-deferred-approval-resume.md),
[ADR 0028](./docs/adr/0028-hybrid-retrieval-and-decay-ranked-demotion.md),
[ADR 0029](./docs/adr/0029-graduated-authority-tiers.md),
[ADR 0030](./docs/adr/0030-degraded-disposition-and-grounding.md),
[ADR 0031](./docs/adr/0031-approval-context-payload.md),
[ADR 0032](./docs/adr/0032-bitemporal-fact-store.md),
[ADR 0033](./docs/adr/0033-two-step-restatement.md),
[ADR 0034](./docs/adr/0034-operational-memory-journal.md),
[ADR 0035](./docs/adr/0035-fallback-chain.md),
[ADR 0036](./docs/adr/0036-read-side-freshness.md),
[ADR 0037](./docs/adr/0037-session-rehydration.md),
[ADR 0038](./docs/adr/0038-evidence-capture-hook.md),
[ADR 0039](./docs/adr/0039-fifteenth-code-audit.md)).
Every L2/L3 change is additive to the L1 Protocols: new optional
parameters, new modules, and side-by-side Protocols; nothing in the L1
surface was removed. The package imports and type-checks with no
optional dependencies installed.

See [CLAUDE.md](./CLAUDE.md) for repository structure and conventions.

## Layout

- `agents/` operator CLI (`python -m agents`)
- `workloads/` individual agent workloads + loader (in-tree and out-of-tree)
- `skills/` Agent Skills bundles, registry, dispatchers, install sources
- `harness/` contracts, enforcement, runtime adapter, budgets, events
- `memory/` namespace-bound stores and production adapters
- `evaluation/` behavioural regression gate (dispatch P@1/MRR, trajectory)
- `tests/` test suite (mirrors the source layout)
- `docs/` architecture, ADRs, the L2 backlog, generated JSON Schema
- `scripts/` operational and developer scripts

## Capabilities

- **Harness.** Behavioral contracts (pre/invariant/post/governance,
  hard/soft severity), `run_under_contract` enforcement with opt-in
  default-path wiring (skill-contract composition, drift recording +
  threshold events, recovery directives, run-scoped lifecycles), action
  budgets (steps/tokens/wall-clock/tool-calls, per-tool quotas, plus a
  cost dimension and per-tool token/wall-clock caps, cumulative across
  an approval pause), structured OTel-ready events, Jensen-Shannon
  distributional drift, and opt-in self-attesting run-provenance
  records (`record_sink`, `contract_digest`, `verify_run_record`, the
  `scripts/check_run_records.py` offline gate).
- **Provider batch capabilities (optional extras).**
  `AnthropicBatchProcessor` (Message Batches) and
  `cache_control_system` (prefix-stable prompt caching) under the
  `anthropic` extra; `OpenAIBatchProcessor` (OpenAI Batch API) under
  the `openai` extra. Async bulk at roughly 50% token price; lazily
  imported, the package type-checks without either SDK.
- **Runtime adapter.** `PydanticAIRuntime` wires the guard and budget
  into the tool-call path: every local *and* MCP tool call passes the
  same guard gate (approve / reject / require-approval), a wall-clock
  watchdog (preempts at an await boundary), streaming budget
  enforcement, a pause/`ResumableState`/resume approval flow (replay
  by default; opt-in `approval_mode="deferred"` continues from the
  paused leg's message history so prior tool calls run exactly once,
  `BL-114`), an opt-in `RetryPolicy` (backoff + circuit breaker), an
  opt-in structured soft-reject, and an opt-in `model_settings`
  pass-through with prompt-cache token surfacing (`BL-132`/`BL-171`:
  Anthropic cache breakpoints ride `AnthropicModelSettings`; cache
  hit/creation counts land on the `BudgetTracker`, not charged to
  `max_tokens`). Provider selection and credentials:
  [docs/runtime-providers.md](./docs/runtime-providers.md).
- **Memory.** Namespace-bound `MemoryStore` with `InMemoryStore`
  reference plus `SQLiteStore`, `RedisStore`, `S3Store`, `DynamoDBStore`
  adapters; extension Protocols for batch, cursor scan,
  content-addressing, CAS, MVCC version tokens
  (`VersionedMemoryStore`), atomic multi-key transactions
  (`TransactionalMemoryStore`), and similarity query
  (`SemanticMemoryStore` + `InMemorySemanticStore`); `TTLSweeper`;
  `MemoryCompactor` with the `Summarizer` Protocol and
  `TruncatingSummarizer` reference (version-gated compaction) and
  `TieredMemoryStore` (hot/cold tiering, CAS-guarded promotion);
  transparent `EncryptedStore` (AES-256-GCM) with static / env / file
  / rotating (`VersionedKeyProvider`) key providers, and `ACLStore`
  with role and attribute-based (`AttributeACL`) policies and an
  audited `AccessDenied` event, both with `wrap_encrypted` / `wrap_acl`
  forwarding the wrapped backend's extension Protocols truthfully;
  optional audit events.
- **Evaluation.** A behavioural regression gate: `evaluate_dispatch`
  (P@1 / MRR over a JSON golden set) and `evaluate_trajectory`
  (expected vs actual contract terminal outcome), deterministic and
  network-free, run as a blocking CI job via `scripts/eval.py`.
- **Skills.** Agent Skills spec-compliant loader/registry, skill
  versioning (`name@version`), seven router dispatchers (the five core
  keyword, LLM, lane, routing-chain, skill-based, plus the L2
  multi-ensemble and embedding), an `InstrumentedDispatcher` telemetry
  wrapper, and a `default_dispatcher` factory for the recommended
  instrumented chain; a deterministic `HashingEmbeddingProvider`;
  skill-level contracts; and pluggable install sources (local, GitHub,
  marketplace) with bounded symlink-safe extraction, optional checksum
  and signature verification, and gated contract execution for
  untrusted bundles.
- **CLI.** `python -m agents workloads list | skills list | skills
  install <name> --from <src> | run <wl> <q> [--json]`.

## Install

```bash
uv sync --all-extras        # dev: every adapter + test doubles
```

Production backends are optional extras, lazily imported:

```bash
pip install 'agents[redis]'   # RedisStore
pip install 'agents[aws]'     # S3Store, DynamoDBStore
pip install 'agents[crypto]'  # EncryptedStore (AES-256-GCM)
pip install 'agents[otel]'    # OTelSink (OTLP/HTTP)
```

## Build and test

```bash
make check     # ruff + mypy + pytest
make schema    # regenerate docs/schema/*.json from the models
uv run python scripts/eval.py   # the BL-130 dispatch regression gate
```

## Project status and security

Pre-1.0 infrastructure. See [STATUS.md](./STATUS.md) for phase and
document maturity, [LIMITATIONS.md](./LIMITATIONS.md) for explicit scope
boundaries and known gaps, [CHANGELOG.md](./CHANGELOG.md) for material
changes, [docs/releasing.md](./docs/releasing.md) for the versioning,
release, and operations policy, and [SECURITY.md](./SECURITY.md) for
the hardening posture and disclosure process. Roadmap:
[docs/backlog.md](./docs/backlog.md); decisions:
[docs/adr/](./docs/adr/README.md).

## License

Apache License 2.0. See [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
