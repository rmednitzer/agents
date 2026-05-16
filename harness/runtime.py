"""Runtime adapter protocol.

The runtime adapter abstracts the agent framework powering a workload.
The harness contract is:

- Workloads receive a Runtime instance, not a framework type.
- Swapping runtimes (e.g. PydanticAI -> LangGraph -> Smolagents) is a
  Protocol-conformance check, not a workload rewrite.
- Sandboxing, action budgets, tool-use authorization, and observability
  are enforced by the harness around the Runtime, not delegated to it.

Default adapter: PydanticAIRuntime. See docs/adr/0001-runtime-selection.md.

Phase 2 extends the Protocol with budget, mcp_servers, and guard
parameters. See docs/adr/0003-budgets-mcp-guards.md.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from harness.budgets import BudgetTracker
from harness.guard import ToolGuard
from harness.mcp import MCPServerSpec


@runtime_checkable
class Runtime(Protocol):
    """Adapter contract between a workload and the underlying agent framework.

    Implementations must be async-safe and side-effect-free at construction.
    All run-scoped state is held by the harness, not the runtime instance.
    """

    name: str

    async def run(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
    ) -> Any:
        """Execute a single agent run to completion.

        Args:
            prompt: User prompt or task description.
            tools: Locally-defined tool definitions for this run.
            deps: Dependency object injected into tool calls.
            budget: Action budget tracker. Adapters must call
                consume_step / consume_tokens / consume_tool_call /
                check_wall_clock at appropriate points so the tracker
                can enforce limits and emit BudgetExceededEvent.
            mcp_servers: MCP server specs the adapter should start before
                the run and stop after. Tools exposed by these servers
                are merged with the `tools` list.
            guard: Tool guard invoked before each proposed tool call.
                Adapters must respect GuardDecision (APPROVE / REJECT /
                REQUIRE_APPROVAL). On REJECT with HARD severity, raise
                GovernanceViolation. On REQUIRE_APPROVAL, capture the
                proposed action and surface it through the harness's
                ResumableState mechanism.

        Returns:
            Framework-specific result. The harness validates against the
            workload's declared output schema before exposing it upstream.
        """
        ...

    def stream(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
    ) -> AsyncIterator[Any]:
        """Stream incremental output from an agent run.

        Same parameter semantics as run(). Streaming events are
        framework-specific. The harness normalizes them to OTel GenAI
        semantic conventions before forwarding.
        """
        ...


class PydanticAIRuntime:
    """Default runtime adapter backed by PydanticAI.

    Stub implementation. The methods raise NotImplementedError until the
    first workload is wired in. Until then, the type exists so the Protocol
    surface is fixed and downstream code can be written against it.
    """

    name: str = "pydantic-ai"

    def __init__(self, model: str) -> None:
        """Construct an adapter bound to a specific model identifier.

        Args:
            model: PydanticAI model string. Examples:
                'anthropic:claude-opus-4-7', 'openai:gpt-4o',
                'ollama:qwen3:30b-a3b'.
        """
        self.model = model

    async def run(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
    ) -> Any:
        raise NotImplementedError(
            "PydanticAIRuntime.run is a stub; implement when first workload lands"
        )

    def stream(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
    ) -> AsyncIterator[Any]:
        raise NotImplementedError(
            "PydanticAIRuntime.stream is a stub; implement when first workload lands"
        )
