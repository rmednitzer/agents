# Agents

Infrastructure repository for agentic workloads: runtime, skills, harness, memory.

## Status

L1 framework plus the full L2 implementation wave, merged to `main` (see
[docs/backlog.md](./docs/backlog.md), [ADR 0007](./docs/adr/0007-l2-implementation-wave.md)).
Every L2 change is additive to the L1 Protocols: new optional parameters,
new modules, and side-by-side Protocols; nothing in the L1 surface was
removed. The package imports and type-checks with no optional
dependencies installed.

See [CLAUDE.md](./CLAUDE.md) for repository structure and conventions.

## Layout

- `agents/` operator CLI (`python -m agents`)
- `workloads/` individual agent workloads + loader (in-tree and out-of-tree)
- `skills/` Agent Skills bundles, registry, dispatchers, install sources
- `harness/` contracts, enforcement, runtime adapter, budgets, events
- `memory/` namespace-bound stores and production adapters
- `tests/` test suite (mirrors the source layout)
- `docs/` architecture, ADRs, the L2 backlog, generated JSON Schema
- `scripts/` operational and developer scripts

## Capabilities

- **Harness.** Behavioral contracts (pre/invariant/post/governance,
  hard/soft severity), `run_under_contract` enforcement, action budgets
  (steps/tokens/wall-clock/tool-calls, plus per-tool quotas), structured
  OTel-ready events, contract composition, soft-violation recovery
  handlers, and Jensen-Shannon distributional drift.
- **Runtime adapter.** `PydanticAIRuntime` wires the guard and budget
  into the tool-call path: every local *and* MCP tool call passes the
  same guard gate (approve / reject / require-approval), a preemptive
  wall-clock watchdog, streaming budget enforcement, and a
  pause/`ResumableState`/resume approval flow. Provider selection and
  credentials: [docs/runtime-providers.md](./docs/runtime-providers.md).
- **Memory.** Namespace-bound `MemoryStore` with `InMemoryStore`
  reference plus `SQLiteStore`, `RedisStore`, `S3Store`, `DynamoDBStore`
  adapters; extension Protocols for batch, cursor scan,
  content-addressing, and CAS; `TTLSweeper`; transparent `EncryptedStore`
  (AES-256-GCM) and per-key `ACLStore`; optional audit events.
- **Skills.** Agent Skills spec-compliant loader/registry, skill
  versioning (`name@version`), eight dispatchers (the five core routers
  keyword, LLM, lane, routing-chain, skill-based, plus the L2
  multi-ensemble and embedding) and an instrumented telemetry wrapper,
  skill-level contracts, and pluggable install
  sources (local, GitHub) with bounded extraction, optional checksum
  pinning, and gated contract execution for untrusted bundles.
- **CLI.** `python -m agents workloads list | skills list | run <wl> <q>`.

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
```

## Project status and security

Pre-1.0 infrastructure. See [STATUS.md](./STATUS.md) for phase and
document maturity, [LIMITATIONS.md](./LIMITATIONS.md) for explicit scope
boundaries and known gaps, [CHANGELOG.md](./CHANGELOG.md) for material
changes, and [SECURITY.md](./SECURITY.md) for the hardening posture and
disclosure process. Roadmap: [docs/backlog.md](./docs/backlog.md);
decisions: [docs/adr/](./docs/adr/README.md).

## License

Apache License 2.0. See [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
