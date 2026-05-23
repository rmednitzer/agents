# docs/

Architecture documentation, ADRs (Architecture Decision Records), and design notes.

ADRs live under `docs/adr/` (see the [ADR index](./adr/README.md)) and follow the standard ADR template: status, context, decision, consequences, revisit triggers. ADR 0001-0006 cover the L1 framework; [ADR 0007](./adr/0007-l2-implementation-wave.md) records the L2 wave; [ADR 0008](./adr/0008-l3-security-hardening-and-roadmap.md) records L3 entry, security hardening, and the validated roadmap; [ADR 0009](./adr/0009-code-audit-hardening.md) the first full audit; [ADR 0010](./adr/0010-l3-default-path-wiring-and-audit-wave.md) the L3 default-path-wiring + second-audit wave; [ADR 0011](./adr/0011-third-audit-and-l3-capability-wave.md) the third-audit + L3 capability wave (key providers, ABAC, MVCC tokens, semantic memory, the evaluation gate); [ADR 0012](./adr/0012-run-provenance-and-anthropic-capabilities.md) opt-in run-provenance records and the optional Anthropic / OpenAI batch capabilities; [ADR 0013](./adr/0013-fifth-code-audit.md) the fifth code audit (read-vs-listing expiry boundary, OpenAI batch error label, `LocalSkillSource` symlink-safe clear, JSON span-list memory ceiling, provenance-gate registry validation).

Repository-level status and scope: [STATUS.md](../STATUS.md) (phase and document maturity), [LIMITATIONS.md](../LIMITATIONS.md) (scope boundaries and known gaps), [CHANGELOG.md](../CHANGELOG.md) (material changes by phase).

- `runbook.md`: the maintenance runbook (audit, review, enhance, validate, extend; the per-`.md` sweep procedure; the cycle calendar).
- `runtime-providers.md`: how a workload selects a model and how `PydanticAIRuntime` reaches the Anthropic or OpenAI API (credentials, the `Runtime` boundary, testing without keys).
- `releasing.md`: the versioning and release policy, the tag-triggered release workflow, and operational notes (deploy, rollback, per-backend memory backup/restore).
- `backlog.md`: the line-item tracker. L2 (BL-001 .. BL-090, all resolved at merge commit `af1df9d`) and L3 (BL-1xx; the default-path-wiring wave, the L3 capability wave, the run-provenance + batch capabilities, and the fifth-audit fixes resolved per ADR 0010 / 0011 / 0012 / 0013, plus the post-audit `BL-193` approval-resume argument-binding fix; the rest pending), with per-item status; the source of truth for what shipped and where.
- `schema/`: generated JSON Schema for `manifest.yaml`, SKILL.md frontmatter, and the `RunRecord` provenance record (do not edit by hand; see `scripts/gen_schema.py`).
