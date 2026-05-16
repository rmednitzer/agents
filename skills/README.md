# skills/

Reusable skill definitions following the Anthropic skill convention. Each skill is a directory with a `SKILL.md` at the root, optional `references/` for static knowledge, and optional `scripts/` and `assets/` for bundled code and data.

The skill description in YAML frontmatter must be specific enough that a router selects it precisely. Generic descriptions degrade routing quality.

See [CLAUDE.md](../CLAUDE.md) for conventions.
