# skills/

Reusable skill definitions following the Anthropic skill convention. Each skill is a directory with a `SKILL.md` at the root, optional `references/` for static knowledge, and optional `scripts/` and `assets/` for bundled code and data.

The skill description in YAML frontmatter must be specific enough that a router selects it precisely. Generic descriptions degrade routing quality.

`SkillRegistry` indexes by name and lane and supports versioning (`name@version`, latest-wins, rollback by re-add). Dispatchers: keyword, LLM, lane, routing-chain, skill-based, `MultiDispatcher` (vote/average/weighted ensemble), `EmbeddingDispatcher` (pluggable `EmbeddingProvider`), and `InstrumentedDispatcher` (latency / fallback-rate / `DispatchObserved`). A skill may ship `contract.py`; `Skill.contract()` loads it and `harness.compose_contracts` composes it with the workload contract. Install bundles via `SkillSource` (`LocalSkillSource`, `GitHubSkillSource`); both reject path-traversing names and archive members. `GitHubSkillSource` also bounds the download, member count, and uncompressed size, and takes an optional `sha256`; `install_skill` does not execute a bundled `contract.py` unless `allow_contract=True`. See [ADR 0006](../docs/adr/0006-skills-and-dispatcher.md), [ADR 0007](../docs/adr/0007-l2-implementation-wave.md), and [ADR 0008](../docs/adr/0008-l3-security-hardening-and-roadmap.md).

See [CLAUDE.md](../CLAUDE.md) for conventions.
