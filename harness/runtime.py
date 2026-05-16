"""Runtime adapter protocol.

The runtime adapter abstracts the agent framework powering a workload.
The harness contract is:

- Workloads receive a Runtime instance, not a framework type.
- Swapping runtimes (e.g. PydanticAI -> LangGraph -> Smolagents) is a
  Protocol-conformance check, not a workload rewrite.
- Sandboxing, action budgets, tool-use authorization, and observability
  are enforced by the harness around the Runtime, not delegated to it.

Default adapter: PydanticAIRuntime. See docs/adr/0001-runtime-selection.md.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable


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
        max_steps: int | None = None,
    ) -> Any:
        """Execute a single agent run to completion.

        Args:
            prompt: User prompt or task description.
            tools: Tool definitions registered for this run.
            deps: Dependency object injected into tool calls.
            max_steps: Action-budget cap. None defers to harness default.

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
        max_steps: int | None = None,
    ) -> AsyncIterator[Any]:
        """Stream incremental output from an agent run.

        Streaming events are framework-specific. The harness normalizes
        them to OTel GenAI semantic conventions before forwarding.
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
        max_steps: int | None = None,
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
        max_steps: int | None = None,
    ) -> AsyncIterator[Any]:
        raise NotImplementedError(
            "PydanticAIRuntime.stream is a stub; implement when first workload lands"
        )
