"""Tests for harness.runtime Phase 2 Protocol surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from harness.budgets import BudgetTracker
from harness.guard import ToolGuard
from harness.interruption import ResumableState
from harness.mcp import MCPServerSpec
from harness.runtime import PydanticAIRuntime, Runtime


class _ProtocolCompliantRuntime:
    name: str = "test"

    async def run(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
        resume: ResumableState | None = None,
    ) -> Any:
        return "ok"

    def stream(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
        resume: ResumableState | None = None,
    ) -> AsyncIterator[Any]:
        raise NotImplementedError


def test_runtime_protocol_accepts_phase2_kwargs() -> None:
    """A runtime that takes budget, mcp_servers, and guard satisfies the Protocol."""
    rt = _ProtocolCompliantRuntime()
    assert isinstance(rt, Runtime)


def test_pydantic_ai_runtime_satisfies_protocol() -> None:
    """The default stub still satisfies the extended Protocol."""
    rt = PydanticAIRuntime(model="anthropic:claude-opus-4-7")
    assert isinstance(rt, Runtime)
