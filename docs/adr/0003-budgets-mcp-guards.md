# ADR 0003: Action budgets, MCP server lifecycle, tool guards

- Status: Accepted
- Date: 2026-05-16
- Authors: rmednitzer
- Builds on: ADR 0002 (behavioral contracts)

## Context

Phase 1 shipped the contract surface but left three items scaffolded without live wiring: governance predicates, approval interruptions, and the action budget. Phase 1 also did not address how MCP servers fit into the workload-harness boundary. This ADR fixes all three.

The shape of the runtime-side hook is constrained by the convergent patterns across vendor SDKs. Anthropic's Claude Agent SDK uses hooks (PreToolUse, PostToolUse, Stop). OpenAI Agents SDK uses guardrails with tripwires and needs_approval. Google ADK uses callbacks at agent / model / tool boundaries. All three expose a synchronous decision point before each tool call and a separate budget concept. The harness can adopt the same shape without coupling to any one vendor.

MCP server lifecycle is the other side of the same boundary. A workload declares which MCP servers it needs; the runtime adapter starts them at run begin, exposes their tools, and stops them at run end. The harness owns the lifecycle policy (allowlists, timeouts, transport choice); the adapter owns the execution.

## Decision

### 1. ActionBudget and BudgetTracker

ActionBudget is an immutable Pydantic model with four optional fields: max_steps, max_tokens, max_wall_clock_seconds, max_tool_calls. None means unlimited for that dimension.

BudgetTracker is the per-run mutable counter. It is the only mutable harness object. The Runtime adapter receives a BudgetTracker via Runtime.run(budget=...) and is responsible for calling consume_step / consume_tokens / consume_tool_call / check_wall_clock at appropriate checkpoints. On overflow, the tracker emits a BudgetExceededEvent and raises BudgetExceeded; the exception propagates through the runtime back to run_under_contract.

Wall-clock enforcement is reactive: callers must invoke check_wall_clock at known points (typically before each step). Background timing (signal handlers, watchdog tasks) is out of scope for L1.

### 2. MCPServerSpec, MCPTransport, MCPLifecycle

MCPServerSpec is an immutable Pydantic model declaring a single MCP server with name, transport (stdio / http / sse), transport-specific connection fields (command + args for stdio, url + headers for http and sse), timeout, and optional allowlist of tool names. A model validator enforces that stdio specs have a command and http/sse specs have a url.

MCPLifecycle is the adapter contract: start(spec) -> handle, stop(handle), list_tools(handle). The adapter owns implementation. The default PydanticAIRuntime will use PydanticAI's built-in MCP integration once a workload requires it; until then, the surface is fixed.

Workloads declare MCP servers in their WorkloadManifest (Phase 4); they never instantiate MCP clients directly. The runtime adapter receives the list via Runtime.run(mcp_servers=...).

### 3. ToolGuard, GuardResponse, HarnessToolGuard

A ToolGuard is the runtime-side hook invoked before each proposed tool call. Its check(tool, arguments) returns a GuardResponse with one of three decisions:

- APPROVE: the runtime proceeds with the call.
- REJECT: the runtime aborts the call. On HARD severity the harness raises GovernanceViolation; on SOFT severity the runtime is expected to log and continue (the guard has already emitted the violation event).
- REQUIRE_APPROVAL: the runtime captures the proposal and the harness produces a ResumableState for HITL approval.

HarnessToolGuard is the default implementation. It binds to a Contract and consults the contract's governance predicates and approval_required list. Governance predicates take a ProposedAction (frozen dataclass: tool + arguments). HarnessToolGuard runs governance predicates in declaration order; the first HARD failure produces REJECT and emits GovernanceViolated. SOFT failures emit GovernanceViolated and continue. After governance, if the tool is in approval_required, the guard emits ApprovalRequested and returns REQUIRE_APPROVAL.

When run_under_contract is called with a contract that has governance predicates or approval_required entries, and no explicit guard is passed, a HarnessToolGuard is constructed automatically.

### 4. Runtime Protocol extension

Runtime.run and Runtime.stream gain three keyword-only parameters:

- budget: BudgetTracker | None
- mcp_servers: list[MCPServerSpec] | None
- guard: ToolGuard | None

The old max_steps parameter is removed; budget supersedes it. No real workloads existed yet, so this is not a breaking change in practice.

## Consequences

Positive:

- The runtime-side hook for governance and approval is now real surface, not a Phase 1 placeholder. A runtime that follows the Protocol can be fully enforced.
- Budget enforcement is integrated cleanly: one mutable object, all dimensions tracked, exhaustion is an exception not a return value.
- MCP server lifecycle is declared once and managed by the adapter. The same WorkloadManifest can be deployed against any Runtime adapter that implements MCPLifecycle.
- ToolGuard is a single Protocol so workloads or tests can substitute custom guards (e.g. a stricter guard for production, a permissive guard for tests).

Negative:

- The interruption-resume flow for REQUIRE_APPROVAL requires the runtime adapter to actually pause execution. PydanticAI does not yet expose a clean pause-and-resume primitive, so the adapter implementation will likely simulate it via an exception-and-catch pattern. This is documented as adapter-level work, not L1 work.
- Wall-clock enforcement is reactive. A pathological tool call that never returns will not be killed; only the next checkpoint will detect overrun. Background watchdog is L2.

Neutral:

- ProposedAction is a frozen dataclass rather than a Pydantic model. Governance predicates are called by the harness, so JSON ser/de is not required.

## Alternatives considered and rejected

- A single hook accepting all decision data (budget remaining, action, conversation state) instead of separate budget + guard. Rejected because it conflates concerns and would force the harness to expose conversation state, which the Protocol intentionally hides.
- Putting budget enforcement inside the guard (each tool call checks the budget). Rejected because step and token consumption happens between tool calls too; the budget belongs to the run loop, not to the tool boundary.
- Wrapping the Runtime in a decorator that injects the guard. Rejected because that hides the surface; explicit parameters make the Protocol obvious to readers.

## Deferred to L2

- Live interruption-resume mid-run in the PydanticAI adapter. Adapter-level work, lands with the first real workload.
- Background watchdog for wall-clock enforcement.
- Per-tool quotas (e.g. allow up to 3 calls to search, up to 1 call to delete). Currently a single tool_calls counter applies to all tools.
- Streaming budget enforcement (token accumulation during a stream).

## Revisit triggers

This decision is revisited if:

- PydanticAI ships a native pause-and-resume primitive; the interruption flow may be simplified.
- The three-decision GuardResponse (APPROVE / REJECT / REQUIRE_APPROVAL) is insufficient and we find ourselves wanting more outcomes (e.g. RETRY, DELEGATE).
- Per-tool budget quotas become a recurring requirement.
