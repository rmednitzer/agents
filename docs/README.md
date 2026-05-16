# docs/

Architecture documentation, ADRs (Architecture Decision Records), and design notes.

ADRs live under `docs/adr/` and follow the standard ADR template: status, context, decision, consequences. ADR 0001-0006 cover the L1 framework; [ADR 0007](./adr/0007-l2-implementation-wave.md) records the cross-cutting decisions for the L2 implementation wave.

- `runtime-providers.md`: how a workload selects a model and how `PydanticAIRuntime` reaches the Anthropic or OpenAI API (credentials, the `Runtime` boundary, testing without keys).
- `backlog.md`: the L2 line-item tracker (BL-001 .. BL-090) with per-item status; the source of truth for what shipped and where.
- `schema/`: generated JSON Schema for `manifest.yaml` and SKILL.md frontmatter (do not edit by hand; see `scripts/gen_schema.py`).
