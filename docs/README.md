# docs/

Architecture documentation, ADRs (Architecture Decision Records), and design notes.

ADRs live under `docs/adr/` (see the [ADR index](./adr/README.md)) and follow the standard ADR template: status, context, decision, consequences, revisit triggers. ADR 0001-0006 cover the L1 framework; [ADR 0007](./adr/0007-l2-implementation-wave.md) records the L2 wave; [ADR 0008](./adr/0008-l3-security-hardening-and-roadmap.md) records L3 entry, security hardening, and the validated roadmap.

Repository-level status and scope: [STATUS.md](../STATUS.md) (phase and document maturity), [LIMITATIONS.md](../LIMITATIONS.md) (scope boundaries and known gaps), [CHANGELOG.md](../CHANGELOG.md) (material changes by phase).

- `runtime-providers.md`: how a workload selects a model and how `PydanticAIRuntime` reaches the Anthropic or OpenAI API (credentials, the `Runtime` boundary, testing without keys).
- `backlog.md`: the line-item tracker. L2 (BL-001 .. BL-090, all resolved at merge commit `af1df9d`) and L3 (BL-1xx, pending), with per-item status; the source of truth for what shipped and where.
- `schema/`: generated JSON Schema for `manifest.yaml` and SKILL.md frontmatter (do not edit by hand; see `scripts/gen_schema.py`).
