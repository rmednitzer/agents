# ADR 0006: Skills framework and dispatcher

- Status: Accepted
- Date: 2026-05-16
- Authors: rmednitzer
- Builds on: ADR 0001 (runtime selection), ADR 0005 (workload bundles)

## Context

A workload bundle (ADR 0005) declares its identity, runtime, memory binding, MCP servers, action budget, and a list of `skills` plus an optional `dispatcher`. ADR 0005 deferred the skills definition; this ADR fills it in.

Two constraints drove the design:

1. **Cross-vendor compatibility**. The Agent Skills open standard (Anthropic, December 2025, agentskills.io) defines the SKILL.md format and was adopted by 32 tools by March 2026 (Claude Code, Codex CLI, Cursor, VS Code, Gemini CLI, Kiro, Goose, JetBrains, and others). Defining a parallel skill format would isolate the framework from this ecosystem. The framework must implement the open spec exactly.

2. **Hierarchical routing reality**. Real skill stacks scale into the tens or hundreds. A flat dispatcher does not scale: 50 skills with one-shot routing is high-variance, slow, and hard to audit. The framework must support hierarchical dispatch (lanes, routing chains, skill-based meta-routers) without forcing every workload to adopt it.

## Decision

### 1. SkillManifest matches the Agent Skills spec exactly

`SkillManifest` (Pydantic frozen) carries the spec's frontmatter fields:

- `name` (required, 1-64 chars, lowercase alphanumeric + hyphens, no leading/trailing/consecutive hyphens).
- `description` (required, 1-1024 chars).
- `license` (optional).
- `compatibility` (optional, 1-500 chars).
- `metadata` (optional, str -> str open extension point).
- `allowed_tools` (optional, space-separated string, exposed as `allowed-tools` via Pydantic alias to match spec).

The loader validates that the SKILL.md's parent directory name matches the `name` field, per spec. Framework-specific extensions (lane, triggers, namespace) live in the `metadata` map, so a skill remains spec-compliant when consumed by Claude Code or Codex CLI; they just ignore unrecognized metadata.

### 2. Skill class wraps the manifest with lazy resources

`Skill` is a regular dataclass (mutable wrapper) holding:

- `manifest: SkillManifest` (frozen).
- `path: Path` (skill directory).
- `references: dict[str, Path]` (eagerly indexed at discovery time).
- `scripts: dict[str, Path]`.
- `assets: dict[str, Path]`.
- `_body: str | None` (lazy-loaded on first `body()` call).

Progressive disclosure per the spec:

- Eagerly load: name + description (cheap, needed for dispatch).
- Lazy load: body and references (expensive, loaded only when the skill is activated).

### 3. SkillRegistry indexes by name and by lane

`SkillRegistry` discovers skills under a root directory, indexes them by name, and maintains a lane index (lane name -> list of skill names). `from_directory(root)` is the primary constructor; `add(skill)` is available for programmatic construction in tests.

### 4. Dispatcher Protocol is pure

`Dispatcher` is a Protocol with a single `async dispatch(query, *, context, limit) -> list[SkillMatch]` method. Dispatchers do not invoke skills; they return matches. The calling workload decides what to do. Dispatchers are pure (no side effects); structured logging happens at the harness layer via the `SkillDispatched` event from Phase 1.

`SkillMatch` carries skill_name, confidence (0..1, Pydantic-validated), rationale (audit), and dispatcher (which dispatcher produced this match).

### 5. Five reference dispatchers

The framework ships five dispatchers covering the common patterns:

- **KeywordDispatcher**: deterministic. Scores by metadata triggers and description token overlap. Zero LLM cost. Use as the first stage of a routing chain.
- **LLMDispatcher**: uses a Runtime to pick. Higher cost. Handles ambiguity that keyword scoring misses. Catalog limited to `max_candidates` to bound prompt size.
- **LaneDispatcher**: hierarchical. A router dispatcher selects a lane name; per-lane dispatchers handle within-lane selection. Mirrors the operating contract's assurance-dispatcher pattern.
- **RoutingChainDispatcher**: cheap-first fallback. Tries dispatchers in order, returns the first match above `threshold`. If none meets the threshold, returns the last non-empty result as best-effort fallback.
- **SkillBasedDispatcher**: the dispatcher logic is itself a SKILL.md. The body becomes the routing prompt to a Runtime. Useful when routing logic is large enough to deserve its own versioned artifact and review process.

### 6. Default dispatcher composition

The recommended composition for workloads (matches the user's expressed preference):

```python
dispatcher = RoutingChainDispatcher(
    [
        KeywordDispatcher(registry),
        SkillBasedDispatcher(registry, "dispatcher-skill", routing_runtime),
        LLMDispatcher(registry, routing_runtime),
    ],
    threshold=0.6,
)
```

Cheap-first: keyword matches resolve trivially; a skill-based router handles the gray-area cases via a versioned routing skill; the LLM dispatcher is the last-resort generic catcher. The routing runtime is intentionally a cheap model (Haiku-tier per the operating contract).

Update (2026-05-17): the `dispatcher-skill` routing skill referenced above now ships in-tree at `skills/dispatcher-skill/` (versioned via `metadata.version`), so this composition is runnable as written rather than depending on a skill the caller must supply.

### 7. The `_example` skill in `skills/_example/`

Ships as the reference skill bundle, exercising the loader, registry, and dispatchers in `tests/`. Demonstrates the SKILL.md format, metadata extensions (lane, triggers), and the absence of resource directories (which is valid per spec).

## Consequences

Positive:

- Skills authored here are usable by every Agent Skills compatible tool unchanged. Spec compliance is a design constraint, not a quality goal.
- Lazy body loading bounds the context cost at dispatch time. Only the manifests are read into memory; bodies load on activation.
- The five dispatchers cover keyword, LLM, hierarchical, fallback-chain, and skill-as-router cases. Workloads compose them; the framework does not pick.
- The `SkillBasedDispatcher` makes routing logic editable as markdown rather than code, which matches the operating practice of treating routing as a versioned artifact.

Negative:

- The framework imposes hyphen-separated naming (no underscores). The spec permits only hyphens; the framework enforces it. Existing internal skill stacks that use underscores need renaming.
- LLM and SkillBased dispatchers depend on a Runtime, which must be wired by the caller. There is no implicit default Runtime; this is intentional (the framework should not silently pick a model).
- The `RoutingChainDispatcher` best-effort fallback (return last non-empty match below threshold) can mask routing failures. Workloads that need strict thresholding should set a high `threshold` and check `confidence` after dispatch.

Neutral:

- `metadata` is `dict[str, str]` per spec, not `dict[str, Any]`. Framework extensions that need structured data (e.g. a list of triggers) encode them as strings (comma-separated). This is the spec's constraint; the framework follows.

## Alternatives considered and rejected

- Defining a custom skill format. Rejected: would isolate the framework from the cross-vendor ecosystem that converged on the Agent Skills open standard.
- Single Dispatcher implementation with strategy parameters. Rejected: dispatchers vary structurally (some are pure functions, some hold a Runtime, some chain others). Separate classes are clearer than a god class.
- Built-in default dispatcher with hardcoded chain. Rejected: workloads have different routing budgets and accuracy requirements. Composition belongs to the workload, not the framework.
- Embedding routing logic in the Contract class. Rejected: contracts are about behavioral guarantees; dispatch is about skill selection. Distinct concerns, distinct types.

## Deferred to L2

- `MultiDispatcher` ensemble that combines results from several dispatchers (vote, average, weighted blend).
- Skill-level contracts (`skills/<name>/contract.py`) that compose with the workload contract. Phase 5 ships skills without per-skill contracts; this is a clean extension when needed.
- Embedding-based dispatcher (vector similarity between query and skill descriptions).
- Skill versioning and rollback (track multiple versions of the same skill).
- Skill installation from registries (Vercel skills.sh marketplace, the anthropics/skills repo).
- Validation of `allowed-tools` against the harness's known tool catalog.
- Performance instrumentation: dispatch latency per dispatcher, cache hit rate, runtime token consumption.
- `python -m agents run <workload> <query>` CLI that loads the workload and dispatches automatically.

## Revisit triggers

This decision is revisited if:

- The Agent Skills spec evolves in a backward-incompatible way (move to TOML, schema additions).
- A common dispatch pattern emerges that none of the five reference dispatchers covers.
- The metadata `str -> str` constraint becomes painful for a real workload (structured trigger data with complex matching rules).
- The `RoutingChainDispatcher` fallback semantics surprise real users in production.
