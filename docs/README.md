# docs/

Architecture documentation, ADRs (Architecture Decision Records), and design notes.

ADRs live under `docs/adr/` (see the [ADR index](./adr/README.md)) and follow the standard ADR template: status, context, decision, consequences, revisit triggers. ADR 0001-0006 cover the L1 framework; [ADR 0007](./adr/0007-l2-implementation-wave.md) records the L2 wave; [ADR 0008](./adr/0008-l3-security-hardening-and-roadmap.md) records L3 entry, security hardening, and the validated roadmap; [ADR 0009](./adr/0009-code-audit-hardening.md) the first full audit; [ADR 0010](./adr/0010-l3-default-path-wiring-and-audit-wave.md) the L3 default-path-wiring + second-audit wave.

Repository-level status and scope: [STATUS.md](../STATUS.md) (phase and document maturity), [LIMITATIONS.md](../LIMITATIONS.md) (scope boundaries and known gaps), [CHANGELOG.md](../CHANGELOG.md) (material changes by phase).

- `runtime-providers.md`: how a workload selects a model and how `PydanticAIRuntime` reaches the Anthropic or OpenAI API (credentials, the `Runtime` boundary, testing without keys).
- `releasing.md`: the versioning and release policy, the tag-triggered release workflow, and operational notes (deploy, rollback, per-backend memory backup/restore).
- `backlog.md`: the line-item tracker. L2 (BL-001 .. BL-090, all resolved at merge commit `af1df9d`) and L3 (BL-1xx; the default-path-wiring wave and its follow-ups resolved per ADR 0010, the rest pending), with per-item status; the source of truth for what shipped and where.
- `schema/`: generated JSON Schema for `manifest.yaml` and SKILL.md frontmatter (do not edit by hand; see `scripts/gen_schema.py`).
