# Agents

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/rmednitzer/agents)

Infrastructure repository for agentic workloads: runtime, skills, harness, memory.

## Status

L1 framework, the full L2 implementation wave, the L3 default-path
wiring + audit wave, the third-audit + L3 capability wave, the
run-provenance + provider-batch wave, and the fifth-code-audit
hardening (plus the post-audit approval-resume binding fix, `BL-193`)
on `main` (see [docs/backlog.md](./docs/backlog.md),
[ADR 0007](./docs/adr/0007-l2-implementation-wave.md),
[ADR 0010](./docs/adr/0010-l3-default-path-wiring-and-audit-wave.md),
[ADR 0011](./docs/adr/0011-third-audit-and-l3-capability-wave.md),
[ADR 0012](./docs/adr/0012-run-provenance-and-anthropic-capabilities.md),
[ADR 0013](./docs/adr/0013-fifth-code-audit.md)).
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
  enforcement, a pause/`ResumableState`/resume approval flow, an opt-in
  `RetryPolicy` (backoff + circuit breaker), and an opt-in structured
  soft-reject. Provider selection and credentials:
  [docs/runtime-providers.md](./docs/runtime-providers.md).
- **Memory.** Namespace-bound `MemoryStore` with `InMemoryStore`
  reference plus `SQLiteStore`, `RedisStore`, `S3Store`, `DynamoDBStore`
  adapters; extension Protocols for batch, cursor scan,
  content-addressing, CAS, MVCC version tokens
  (`VersionedMemoryStore`), and similarity query
  (`SemanticMemoryStore` + `InMemorySemanticStore`); `TTLSweeper`;
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
