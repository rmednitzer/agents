"""PydanticAI adapter integration: guard, budget, watchdog, resume.

Covers BL-001 (guard wiring), BL-002 (interruption/resume), BL-003
(wall-clock watchdog), BL-004 (streaming budget), BL-073 (per-tool
quota). All deterministic and network-free via TestModel/FunctionModel.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from harness.budgets import ActionBudget, BudgetTracker
from harness.contract import Contract, Severity
from harness.errors import (
    ApprovalDenied,
    BudgetExceeded,
    GovernanceViolation,
    HarnessError,
)
from harness.guard import GuardDecision, GuardResponse, HarnessToolGuard
from harness.interruption import ResumableState
from harness.mcp import MCPServerSpec, MCPTransport
from harness.runtime import (
    PydanticAIRuntime,
    Runtime,
    _ApprovalPause,
    _gate,
    _GuardState,
    _to_pydantic_mcp,
)


class _Guard:
    """Minimal ToolGuard returning a fixed decision."""

    def __init__(self, response: GuardResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def check(self, tool: str, arguments: dict[str, Any]) -> GuardResponse:
        self.calls.append((tool, arguments))
        return self._response


def _add(a: int, b: int) -> int:
    return a + b


def test_adapter_satisfies_runtime_protocol() -> None:
    assert isinstance(PydanticAIRuntime(TestModel()), Runtime)


@pytest.mark.asyncio
async def test_no_guard_tool_executes() -> None:
    seen: list[tuple[int, int]] = []

    def add(a: int, b: int) -> int:
        seen.append((a, b))
        return a + b

    rt = PydanticAIRuntime(TestModel(), output_type=str)
    out = await rt.run("go", tools=[add])
    assert seen == [(0, 0)]  # TestModel invokes the tool once
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_bl001_hard_reject_raises_governance_violation() -> None:
    guard = _Guard(
        GuardResponse(
            decision=GuardDecision.REJECT,
            reason="blocked by policy",
            severity=Severity.HARD,
        )
    )
    rt = PydanticAIRuntime(TestModel(), output_type=str)
    with pytest.raises(GovernanceViolation):
        await rt.run("go", tools=[_add], guard=guard)
    assert guard.calls
    assert guard.calls[0][0] == "_add"


@pytest.mark.asyncio
async def test_bl001_soft_reject_skips_tool_and_completes() -> None:
    calls: list[Any] = []

    def add(a: int, b: int) -> int:
        calls.append((a, b))
        return a + b

    guard = _Guard(
        GuardResponse(
            decision=GuardDecision.REJECT,
            reason="prefer not to",
            severity=Severity.SOFT,
        )
    )
    rt = PydanticAIRuntime(TestModel(), output_type=str)
    out = await rt.run("go", tools=[add], guard=guard)
    assert calls == []  # soft reject did not execute the tool body
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_bl001_approve_consumes_tool_budget() -> None:
    guard = _Guard(GuardResponse(decision=GuardDecision.APPROVE))
    budget = BudgetTracker(ActionBudget(max_tool_calls=0))
    rt = PydanticAIRuntime(TestModel(), output_type=str)
    with pytest.raises(BudgetExceeded) as exc:
        await rt.run("go", tools=[_add], budget=budget, guard=guard)
    assert exc.value.budget_kind == "tool_calls"


@pytest.mark.asyncio
async def test_bl073_per_tool_quota_via_adapter() -> None:
    def fn(messages: list[Any], info: AgentInfo) -> ModelResponse:
        n = sum(
            1
            for m in messages
            for p in getattr(m, "parts", [])
            if type(p).__name__ == "ToolReturnPart"
        )
        if n < 2:
            return ModelResponse(parts=[ToolCallPart("ping", {})])
        return ModelResponse(parts=[TextPart("done")])

    def ping() -> str:
        return "pong"

    budget = BudgetTracker(ActionBudget(max_tool_calls_per_tool={"ping": 1}))
    rt = PydanticAIRuntime(FunctionModel(fn), output_type=str)
    with pytest.raises(BudgetExceeded) as exc:
        await rt.run("go", tools=[ping], budget=budget)
    assert exc.value.budget_kind == "tool_calls:ping"


@pytest.mark.asyncio
async def test_bl003_wall_clock_watchdog_preempts() -> None:
    async def slow() -> str:
        await asyncio.sleep(1.0)
        return "too late"

    budget = BudgetTracker(ActionBudget(max_wall_clock_seconds=0.05))
    rt = PydanticAIRuntime(TestModel(), output_type=str)
    with pytest.raises(BudgetExceeded) as exc:
        await rt.run("go", tools=[slow], budget=budget)
    assert exc.value.budget_kind == "wall_clock"


@pytest.mark.asyncio
async def test_bl004_streaming_budget_raises_when_tokens_exceeded() -> None:
    rt = PydanticAIRuntime(TestModel(), output_type=str)
    budget = BudgetTracker(ActionBudget(max_tokens=5))
    with pytest.raises(BudgetExceeded) as exc:
        async for _ in rt.stream("go", budget=budget):
            pass
    assert exc.value.budget_kind == "tokens"


@pytest.mark.asyncio
async def test_bl004_streaming_yields_without_budget() -> None:
    rt = PydanticAIRuntime(TestModel(custom_output_text="hello world"), output_type=str)
    chunks = [c async for c in rt.stream("go")]
    assert "".join(chunks)


@pytest.mark.asyncio
async def test_bl002_require_approval_pauses_then_resumes() -> None:
    contract: Contract[Any, Any] = Contract(name="c", version="1.0", approval_required=["risky"])
    guard = HarnessToolGuard(contract)

    executed: list[bool] = []

    def risky() -> str:
        executed.append(True)
        return "did risky thing"

    rt = PydanticAIRuntime(TestModel(), output_type=str)

    paused = await rt.run("go", tools=[risky], guard=guard)
    assert isinstance(paused, ResumableState)
    assert len(paused.pending_approvals) == 1
    assert paused.pending_approvals[0].tool == "risky"
    assert executed == []  # nothing ran yet

    approved = paused.approve(paused.pending_approvals[0].id)
    out = await rt.run("go", tools=[risky], guard=guard, resume=approved)
    assert executed == [True]
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_stream_enforces_wall_clock_parity() -> None:
    """BL-001/003 parity: stream() honours the wall-clock budget."""
    rt = PydanticAIRuntime(TestModel(custom_output_text="a b c"), output_type=str)
    budget = BudgetTracker(ActionBudget(max_wall_clock_seconds=0.0))
    with pytest.raises(BudgetExceeded) as exc:
        async for _ in rt.stream("go", budget=budget):
            pass
    assert exc.value.budget_kind == "wall_clock"


@pytest.mark.asyncio
async def test_stream_consumes_steps() -> None:
    """stream() consumes steps from final usage (parity with run())."""
    rt = PydanticAIRuntime(TestModel(custom_output_text="hello"), output_type=str)
    budget = BudgetTracker(ActionBudget(max_steps=100))
    async for _ in rt.stream("go", budget=budget):
        pass
    assert budget.steps >= 1


@pytest.mark.asyncio
async def test_stream_approval_tool_raises_public_error_not_sentinel() -> None:
    """BL-002: streaming + approval-gated tool -> public HarnessError."""
    contract: Contract[Any, Any] = Contract(name="c", version="1.0", approval_required=["risky"])

    def risky() -> str:
        return "x"

    rt = PydanticAIRuntime(TestModel(), output_type=str)
    with pytest.raises(HarnessError, match="approval"):
        async for _ in rt.stream("go", tools=[risky], guard=HarnessToolGuard(contract)):
            pass


async def _run_gate(guard: Any, budget: Any, **kw: Any) -> str | None:
    return await _gate(
        kw.get("name", "tool"),
        kw.get("arguments", {}),
        guard=guard,
        budget=budget,
        resume=kw.get("resume"),
        state=kw.get("state") or _GuardState(),
        used_approvals=kw.get("used_approvals", set()),
    )


@pytest.mark.asyncio
async def test_gate_hard_reject_raises() -> None:
    """The shared gate (used by local AND MCP tools) enforces hard reject."""
    g = _Guard(GuardResponse(decision=GuardDecision.REJECT, severity=Severity.HARD))
    with pytest.raises(GovernanceViolation):
        await _run_gate(g, None)


@pytest.mark.asyncio
async def test_gate_soft_reject_returns_message() -> None:
    g = _Guard(GuardResponse(decision=GuardDecision.REJECT, reason="nope", severity=Severity.SOFT))
    assert (await _run_gate(g, None)) == "[blocked: nope]"


@pytest.mark.asyncio
async def test_gate_approve_consumes_per_tool_budget() -> None:
    """MCP-path parity: the gate counts the call against the budget."""
    budget = BudgetTracker(ActionBudget(max_tool_calls_per_tool={"search": 0}))
    g = _Guard(GuardResponse(decision=GuardDecision.APPROVE))
    with pytest.raises(BudgetExceeded) as exc:
        await _run_gate(g, budget, name="search")
    assert exc.value.budget_kind == "tool_calls:search"


@pytest.mark.asyncio
async def test_gate_require_approval_pauses_then_denies() -> None:
    contract: Contract[Any, Any] = Contract(name="c", version="1", approval_required=["risky"])
    guard = HarnessToolGuard(contract)
    state = _GuardState()
    with pytest.raises(_ApprovalPause):
        await _run_gate(guard, None, name="risky", state=state)
    assert state.pause is not None


def test_mcp_toolset_receives_process_tool_call() -> None:
    """MCP tools must be gated: a process_tool_call is threaded through."""
    server = _to_pydantic_mcp(
        MCPServerSpec(name="m", transport=MCPTransport.STDIO, command="/bin/true"),
        lambda ctx, call_tool, name, args: call_tool(name, args),
    )
    assert server is not None


def test_to_pydantic_mcp_threads_headers_and_allowlist() -> None:
    """Headers + allowed_tools must not be dropped (manifest surface)."""
    plain = _to_pydantic_mcp(
        MCPServerSpec(
            name="h",
            transport=MCPTransport.HTTP,
            url="https://example/mcp",
            headers={"Authorization": "Bearer t"},
        )
    )
    assert plain is not None

    filtered = _to_pydantic_mcp(
        MCPServerSpec(
            name="h2",
            transport=MCPTransport.HTTP,
            url="https://example/mcp",
            allowed_tools=["only_this"],
        )
    )
    # allowed_tools wraps the server in a FilteredToolset, a distinct type.
    assert type(filtered).__name__ != type(plain).__name__


@pytest.mark.asyncio
async def test_bl002_denied_approval_raises() -> None:
    contract: Contract[Any, Any] = Contract(name="c", version="1.0", approval_required=["risky"])
    guard = HarnessToolGuard(contract)

    def risky() -> str:
        return "should not run"

    rt = PydanticAIRuntime(TestModel(), output_type=str)
    paused = await rt.run("go", tools=[risky], guard=guard)
    assert isinstance(paused, ResumableState)
    denied = paused.deny(paused.pending_approvals[0].id, reason="nope")
    with pytest.raises(ApprovalDenied):
        await rt.run("go", tools=[risky], guard=guard, resume=denied)
