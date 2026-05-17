# harness/

Orchestration, sandboxing, and execution control for agentic workloads. Enforces sandbox boundaries, tool-use authorization, action budgets, and observability.

Surface: `Contract` + `run_under_contract` (preconditions, invariants, postconditions, governance; hard/soft severity) with opt-in L3 wiring (`skill_contracts` composition, `drift_monitor`/`drift_threshold` + `DriftThresholdCrossed`, `RecoveryOutcome.directive`, run-scoped `lifecycles`, `parent_span_id` for a correlated nested-run span tree; every default reproduces L1/L2); `ActionBudget`/`BudgetTracker` (steps, tokens, wall-clock, tool-calls, per-tool quotas, plus a cost dimension and per-tool token/wall-clock caps, cumulative across an approval pause via `snapshot`/`initial_*`); `PydanticAIRuntime` (guard gate on local + MCP tool calls, `ResumableState` pause/resume, wall-clock watchdog that preempts at an await boundary, streaming budget, opt-in `RetryPolicy` and `soft_reject_as_error`); structured OTel-ready events + sinks (`OTelSink`, `RedactingSink` for secret/PII scrubbing, an `AccessDeniedEvent` for an `ACLStore` denial); `compose_contracts` (strictest severity on a name collision, including governance); `RecoveryHandler`; and `DriftMonitor` (Jensen-Shannon). See [ADR 0007](../docs/adr/0007-l2-implementation-wave.md), [ADR 0008](../docs/adr/0008-l3-security-hardening-and-roadmap.md), [ADR 0010](../docs/adr/0010-l3-default-path-wiring-and-audit-wave.md), and [ADR 0011](../docs/adr/0011-third-audit-and-l3-capability-wave.md).

Connecting a workload to an Anthropic or OpenAI model (provider selection, credentials, the `Runtime` boundary, testing without keys): see [Runtime providers](../docs/runtime-providers.md).

Contract changes here have wide blast radius. Document each contract change in `docs/adr/` and state it in the PR description.

See [CLAUDE.md](../CLAUDE.md) for conventions.
