# CLAUDE.md

Guidance for Claude (and Claude Code) when working in this repository.

## Repository purpose

`agents` is an infrastructure repository for agentic workloads: the runtime environment, skill definitions, orchestration harness, and memory backends that agents depend on. It is the operational substrate, not a single agent.

Scope:
- Workloads: individual agent missions and their entry points.
- Skills: reusable capability definitions, packaged as markdown with optional bundled assets, following the Anthropic skill convention.
- Harness: orchestration, sandboxing, execution control, tool-use contracts.
- Memory: state persistence, retrieval, schema, lifecycle.

Out of scope: model training, dataset curation, model serving infrastructure.

## Architecture

The repo treats agents as composable. A workload is a thin entry point that loads skills, runs inside the harness, and interacts with memory through declared interfaces. Boundaries between these layers are explicit and enforceable.

Component contracts:
- A workload declares: skills it loads, harness it targets, memory namespace, exit conditions.
- A skill declares: trigger description, inputs, outputs, side effects, dependencies.
- The harness enforces: sandbox boundaries, tool-use authorization, action budgets, observability.
- Memory exposes: typed read/write per namespace, retention, isolation, lineage.

## Layout

```
agents/
  agents/      operator CLI package (python -m agents)
  workloads/   individual agent workloads + loader
  skills/      reusable skill definitions, registry, dispatchers
  harness/     orchestration and execution control
  memory/      memory backends, schemas, retrieval
  tests/       test suite, mirrors source layout
  docs/        architecture, ADRs, backlog, generated JSON Schema
  scripts/     operational and developer scripts
```

The L1 framework plus the full L2 wave are implemented; see
`docs/backlog.md` (line-item tracker) and `docs/adr/0007-l2-implementation-wave.md`
(cross-cutting decisions).

## Conventions

Language: Python, pinned at `requires-python = ">=3.12"` in pyproject.toml. CI runs on 3.12.

Formatting: ruff (lint + format) and mypy (strict) are configured in pyproject.toml. Run `make check` (lint + type-check + test) before pushing.

Additive-to-L1 rule (ADR 0007): L2 and later changes are additive to the L1 Protocols. Use new optional keyword parameters (defaults preserving L1 behaviour), new modules, or new Protocols beside the existing ones. Do not remove or change an L1 import path or signature. Surface configuration errors at load time, not mid-run.

Documentation style: no em-dashes and no `--` outside HTML comments (the repo's own markdown rule, dogfooded by `workloads/_example`). Use commas, colons, or parentheses instead.

Naming:
- Workloads: `workloads/<purpose>/` (snake_case, describes mission, not technology).
- Skills: `skills/<skill-name>/SKILL.md` plus optional `references/`, `scripts/`, `assets/`.
- Harness modules: `harness/<concern>.py`.
- Memory backends: `memory/<backend>/` with a thin adapter in `memory/__init__.py`.

Dates and units: SI units, ISO 8601 dates (YYYY-MM-DD), 24h time. Default timezone UTC unless explicit.

Documentation tone: direct, technical, no marketing voice. Each component has a README.md explaining purpose, contract, and example usage.

## Adding components

New workload:
1. Create `workloads/<name>/` with `README.md`, `__main__.py`, `manifest.yaml` (skills loaded, harness target, memory namespace, exit conditions).
2. Add tests under `tests/workloads/<name>/`.
3. Document in `docs/workloads/<name>.md` if non-trivial.

New skill:
1. Create `skills/<name>/SKILL.md` with YAML frontmatter (name, description) per Anthropic convention.
2. The description must be specific enough that a router selects it precisely; avoid generic language.
3. Bundle references under `skills/<name>/references/` if static knowledge is needed.
4. Add invocation tests under `tests/skills/<name>/`.

New harness module:
1. Add `harness/<concern>.py`; document the contract it enforces in a module docstring at the top.
2. Tests under `tests/harness/`.
3. If it changes an existing contract, write a short ADR under `docs/adr/`.

New memory backend:
1. Add `memory/<backend>.py` implementing the `MemoryStore` Protocol (single module; the existing adapters are `memory/{inmemory,sqlite,redis,s3,dynamodb}.py`).
2. Import any third-party driver lazily inside `__init__` with a clear error naming the extra; declare the extra in `[project.optional-dependencies]`. The package must import and type-check with the driver absent.
3. Reuse `memory._audit.MemoryAudit` for the optional `sink`/`base_event_fields` surface; offload blocking I/O via `asyncio.to_thread`; validate keys with `memory.validators`.
4. Implement only the extension Protocols the backend can honour (Batch/Scan/ContentAddressable/CAS/Sweepable); do not fake unsupported ones.
5. Tests under `tests/memory/`, using an in-process double (`fakeredis`, `moto`) guarded by `pytest.importorskip`. Document retention, isolation, and any semantics deviation in `memory/README.md` and the module docstring.

## Quality bar

- Every public function has a type signature.
- Every component has a one-paragraph README explaining its contract.
- Every directory under `workloads/`, `skills/`, `harness/`, `memory/` has a README.md.
- Tests are not optional for harness and memory; advisory for workloads and skills.
- CI (lint, type-check, test) must pass before merge. `python scripts/gen_schema.py --check` guards JSON Schema drift and runs in the suite; regenerate with `make schema` after changing a manifest model.
- Changes stay additive to the L1 Protocols (see Conventions / ADR 0007).

## Risk

This repo defines authority boundaries between humans, agents, and tools. Treat changes to the harness or memory contracts as high-impact:
- Run the full test suite.
- Document the contract change in an ADR under `docs/adr/`.
- State blast radius in the PR description (which components, which contracts, rollback path).

## Build and test

Uses `uv`. Set up: `uv sync --all-extras` (installs every optional backend plus test doubles so CI exercises all adapters).

- `make test` runs pytest.
- `make lint` runs `ruff check`.
- `make type-check` runs `mypy agents harness memory workloads skills`.
- `make check` runs lint + type-check + test (run before pushing).
- `make schema` regenerates `docs/schema/*.json` from the Pydantic models.

The PydanticAI runtime is tested deterministically with `TestModel`/`FunctionModel` (no network or API keys). Optional-backend tests skip cleanly when their driver is absent.

## Contributing

See `CONTRIBUTING.md`. Issues and PRs welcome. Security-relevant findings: see `SECURITY.md`.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.
