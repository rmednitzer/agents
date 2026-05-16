# ADR 0005: Workload bundle convention

- Status: Accepted
- Date: 2026-05-16
- Authors: rmednitzer
- Builds on: ADR 0001 (runtime selection), ADR 0002 (behavioral contracts), ADR 0003 (budgets + MCP + guards), ADR 0004 (memory namespaces)

## Context

The harness has accumulated four boundaries (runtime, contract, memory, MCP/guard/budget) and one binding question: how does a deployable unit declare its use of all four? Without a convention, every workload reinvents how to specify its model, namespace, MCP servers, budget, and entry point.

The convention must satisfy:

- A workload is loadable by name, so the operator can run `python -m workloads.<name>` without knowing internal structure.
- A workload's declaration is human-readable so reviewers can audit it without reading code.
- A workload's contract is enforceable by `run_under_contract`, which means the contract is a real `Contract` object, not a description.
- A workload integrates with future skills routing (Phase 5) without modification, so the manifest carries forward-compatible `skills` and `dispatcher` fields.

The shape across vendor SDKs converges on something close to this: OpenAI's Agent class wraps Tools / Handoffs / Guardrails / Sessions, Anthropic's Claude Agent SDK uses CLAUDE.md + skills, Google's ADK uses LlmAgent with explicit App-level Plugins. None of them ship a portable manifest format; each ties the agent definition to its own runtime classes.

The Agent Skills open standard (December 2025) shows what a minimal portable spec looks like: directory + frontmatter + body. The same idea applies one level up: a workload is a directory + manifest + contract + entry.

## Decision

### 1. A workload is a Python package

A workload lives under `workloads/<name>/` and is a regular Python package with `__init__.py`. The package name matches the manifest's `name` field. Python's import machinery resolves the workload; no custom plugin discovery.

This means `workloads/_example/` is importable as `workloads._example` and the loader uses `importlib.import_module` rather than walking the filesystem.

### 2. The manifest is YAML, validated into a Pydantic model

`manifest.yaml` is the human-edited declaration. The loader parses it into a `WorkloadManifest` (frozen Pydantic). YAML is chosen over TOML or JSON because workload manifests have nested structure (runtime spec, memory namespace, MCP server list, budget, exit conditions) and YAML reads better for that shape.

The `WorkloadManifest` schema carries every L1 boundary:

- `name`, `version`, `description`: identity.
- `runtime`: which adapter and model. `RuntimeSpec` is a separate Pydantic model so it can grow `parameters: dict[str, Any]` without polluting the top level.
- `memory_namespace`: optional `Namespace` from Phase 3.
- `mcp_servers`: list of `MCPServerSpec` from Phase 2.
- `skills`, `dispatcher`: forward-compatible, resolved by `SkillRegistry` in Phase 5. Empty list and `None` are valid.
- `budget`: optional `ActionBudget` from Phase 2.
- `exit_conditions`: free-form dict for workload-specific termination logic.

### 3. The contract is a real `Contract` object

`contract.py` is a Python module that constructs a `Contract` instance and exports it as `contract`. The loader imports the module and asserts the export exists and is a `Contract`.

This is more verbose than a declarative format but keeps predicates as code, which is the only honest representation: predicates are functions, not data.

### 4. The entry point is optional `__main__.py`

A workload may include `__main__.py` exporting `main` as an async callable. This lets the workload be run directly: `python -m workloads.<name>`. The loader resolves `main` if present and exposes it on `LoadedWorkload`.

Workloads without an entry point are still loadable; they're meant to be invoked by an orchestrator that constructs inputs and calls `run_under_contract` directly.

### 5. The `_example` workload is the reference

`workloads/_example/` is shipped as part of the repo, not as a sample. Its contract validates markdown documents against the repository's own style rules (H1 required, no em-dashes, no double-dashes outside HTML comments). This makes it dogfood: the example's contract is exercised continuously, and a future change to style rules surfaces as a test failure in the example.

The example also demonstrates a stub `Runtime` that performs work in-process without an LLM call. This shows readers how to build alternative `Runtime` adapters (test stubs, in-process workers, deterministic validators) that satisfy the same Protocol.

## Consequences

Positive:

- A workload is a directory. Reviewing one means reading at most four files.
- The bundle composes with all four prior phases without ceremony: the manifest references `Namespace`, `MCPServerSpec`, `ActionBudget` directly.
- The loader is small (one file) because it leans on `importlib`. No plugin registry, no entry-point setup.
- The `_example` workload runs without API keys, so CI exercises end-to-end every commit.

Negative:

- YAML adds a runtime dependency (PyYAML). Modest cost, well-known library, broadly available.
- Predicates in `contract.py` are not serializable, so workloads cannot be fully declared in YAML alone. This is a feature: predicates are too important to live in YAML, where typos and type errors would be silent.
- Workloads must be inside the `workloads/` package tree. Out-of-tree workloads need either packaging via `pip install -e .` plus a namespace-package extension, or a loader option to import from an arbitrary path. Out of scope for L1.

Neutral:

- `manifest.yaml` is not validated for matching package directory name. The user might rename the directory and forget to update the manifest. A loader check could be added; it is not L1-critical.

## Alternatives considered and rejected

- Manifest in TOML using `pyproject.toml`-style sections. Rejected: nested structures (especially `mcp_servers: [...]` with per-server validation) are awkward in TOML.
- Manifest in pure Python (a `manifest.py` exporting a `WorkloadManifest` instance). Rejected: trades human-editability for code, losing the value of having a single declarative file.
- Entry-point discovery via `pyproject.toml` `[project.entry-points]`. Rejected: requires the package to be installed, complicates in-tree development.
- Workloads outside the package tree, discovered via filesystem walk. Rejected for L1; reconsider when there is a deployment story.

## Deferred to L2

- A `name`-matches-directory validator at load time.
- Workloads out-of-tree (loaded from arbitrary paths or installed packages).
- Manifest JSON Schema generation for editor autocomplete.
- A `workloads list` CLI command that prints all loadable workload names.
- Skill resolution: validating that `skills` entries exist in the `SkillRegistry`. Lands with Phase 5.

## Revisit triggers

This decision is revisited if:

- YAML proves painful (e.g. anchors and aliases produce surprising behavior).
- The convention forces too much boilerplate for trivial workloads.
- A real deployment requires out-of-tree workload discovery.
