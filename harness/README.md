# harness/

Orchestration, sandboxing, and execution control for agentic workloads. Enforces sandbox boundaries, tool-use authorization, action budgets, and observability.

Surface: `Contract` + `run_under_contract` (preconditions, invariants, postconditions, governance; hard/soft severity), `ActionBudget`/`BudgetTracker` (steps, tokens, wall-clock, tool-calls, per-tool quotas), `PydanticAIRuntime` (guard gate on local + MCP tool calls, `ResumableState` pause/resume, wall-clock watchdog, streaming budget), structured OTel-ready events + sinks (`OTelSink`, `RedactingSink` for secret/PII scrubbing), `compose_contracts`, `RecoveryHandler`, and `DriftMonitor` (Jensen-Shannon). See [ADR 0007](../docs/adr/0007-l2-implementation-wave.md) and [ADR 0008](../docs/adr/0008-l3-security-hardening-and-roadmap.md).

Connecting a workload to an Anthropic or OpenAI model (provider selection, credentials, the `Runtime` boundary, testing without keys): see [Runtime providers](../docs/runtime-providers.md).

Contract changes here have wide blast radius. Document each contract change in `docs/adr/` and state it in the PR description.

See [CLAUDE.md](../CLAUDE.md) for conventions.
