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
  workloads/   individual agent workloads
  skills/      reusable skill definitions
  harness/     orchestration and execution control
  memory/      memory backends, schemas, retrieval
  tests/       test suite, mirrors source layout
  docs/        architecture, ADRs, design notes
  scripts/     operational and developer scripts
```

## Conventions

Language: Python (>=3.12 expected; pin in pyproject.toml when first code lands).

Formatting: ruff for lint and format, mypy for type checks. Configure in pyproject.toml.

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
1. Add `memory/<backend>/` with a clear adapter.
2. Document namespace ownership, retention, isolation guarantees in `memory/<backend>/README.md`.
3. State migration path from prior backends if applicable.

## Quality bar

- Every public function has a type signature.
- Every component has a one-paragraph README explaining its contract.
- Every directory under `workloads/`, `skills/`, `harness/`, `memory/` has a README.md.
- Tests are not optional for harness and memory; advisory for workloads and skills.
- CI must pass before merge once workflows land.

## Risk

This repo defines authority boundaries between humans, agents, and tools. Treat changes to the harness or memory contracts as high-impact:
- Run the full test suite.
- Document the contract change in an ADR under `docs/adr/`.
- State blast radius in the PR description (which components, which contracts, rollback path).

## Build and test

To be defined when first code lands. Convention:
- `make test` runs unit tests.
- `make lint` runs ruff plus mypy.
- `make check` runs both.

## Contributing

See `CONTRIBUTING.md`. Issues and PRs welcome. Security-relevant findings: see `SECURITY.md`.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.
