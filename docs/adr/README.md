# Architecture Decision Records

Each ADR records one cross-cutting decision: status, context, decision,
consequences, and revisit triggers. ADRs are immutable once Accepted;
a later ADR supersedes an earlier one rather than editing it.

| ADR | Title | Status | Scope |
| --- | --- | --- | --- |
| [0001](./0001-runtime-selection.md) | Runtime selection | Accepted | Why a `Runtime` Protocol with a PydanticAI default, not a vendor SDK. |
| [0002](./0002-behavioral-contracts.md) | Behavioral contracts | Accepted | The P/I/G/R contract model, hard/soft severity, events. |
| [0003](./0003-budgets-mcp-guards.md) | Budgets, MCP, guards | Accepted | Action budgets, MCP server lifecycle, tool-use guard gate. |
| [0004](./0004-memory-namespace-contracts.md) | Memory namespace contracts | Accepted | Namespace-bound `MemoryStore`, isolation, retention, extension Protocols. |
| [0005](./0005-workload-bundles.md) | Workload bundles | Accepted | `manifest.yaml`, the loader, in-tree and out-of-tree workloads. |
| [0006](./0006-skills-and-dispatcher.md) | Skills and dispatcher | Accepted | Agent Skills compliance, registry, the dispatcher Protocol. |
| [0007](./0007-l2-implementation-wave.md) | L2 implementation wave | Accepted | Additive-to-L1 rule, lazy backends, the L2 batch delivery. |
| [0008](./0008-l3-security-hardening-and-roadmap.md) | L3 entry, security hardening, validated roadmap | Accepted | Skill-install and event hardening, the tiered L3 roadmap, CI hardening. |
| [0009](./0009-code-audit-hardening.md) | Code audit, additive hardening, errata | Accepted | The full-audit fixes, the tracked gaps, ADR 0005/0006 errata. |
| [0010](./0010-l3-default-path-wiring-and-audit-wave.md) | L3 default-path wiring, audit follow-ups, governance maturity | Accepted | `BL-100`-`104` wiring, `BL-154/156/157/161` follow-ups, cost/retry/structured-reject, REUSE/release, the dispatcher-count erratum. |

See [docs/backlog.md](../backlog.md) for the line-item tracker and
[CLAUDE.md](../../CLAUDE.md) for when a change needs a new ADR.
