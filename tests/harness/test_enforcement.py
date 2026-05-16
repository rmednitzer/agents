"""Tests for harness.enforcement, including Phase 2 budget + guard wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from harness.budgets import ActionBudget, BudgetTracker
from harness.contract import Contract, Severity, predicate
from harness.enforcement import run_under_contract
from harness.errors import (
    BudgetExceeded,
    InvariantViolation,
    PostconditionViolation,
    PreconditionViolation,
)
from harness.events import (
    ContractCompleted,
    ContractStarted,
    PreconditionViolated,
)
from harness.guard import ToolGuard
from harness.interruption import ResumableState
from harness.mcp import MCPServerSpec
from harness.runtime import Runtime
from harness.sinks import MemorySink


class _Input(BaseModel):
    query: str


class _Output(BaseModel):
    text: str


class _StubRuntime:
    """Test runtime that records what the harness passed and returns a fixed Output."""

    name: str = "stub"

    def __init__(self, output: _Output | dict[str, Any]) -> None:
        self._output = output
        self.received_budget: BudgetTracker | None = None
        self.received_mcp_servers: list[MCPServerSpec] | None = None
        self.received_guard: ToolGuard | None = None

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
        self.received_budget = budget
        self.received_mcp_servers = mcp_servers
        self.received_guard = guard
        return self._output

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


class _BudgetBurningRuntime:
    """Test runtime that exhausts the budget tracker's steps limit."""

    name: str = "burner"

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
        if budget is not None:
            while True:
                budget.consume_step()
        return _Output(text="never")

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


@pytest.mark.asyncio
async def test_happy_path_emits_started_and_completed() -> None:
    @predicate(name="non_empty", severity=Severity.HARD)
    def non_empty(s: _Input) -> bool:
        return bool(s.query)

    @predicate(name="output_set", severity=Severity.HARD)
    def output_set(s: _Output) -> bool:
        return bool(s.text)

    contract: Contract[_Input, _Output] = Contract(
        name="t1",
        version="0.1.0",
        preconditions=[non_empty],
        postconditions=[output_set],
    )
    runtime: Runtime = _StubRuntime(_Output(text="ok"))
    sink = MemorySink()
    result = await run_under_contract(
        runtime=runtime,
        contract=contract,
        input=_Input(query="hello"),
        output_model=_Output,
        sink=sink,
    )
    assert isinstance(result, _Output)
    assert result.text == "ok"
    kinds = [e.kind for e in sink.events]
    assert kinds[0] == "contract_started"
    assert kinds[-1] == "contract_completed"


@pytest.mark.asyncio
async def test_hard_precondition_halts() -> None:
    @predicate(name="non_empty", severity=Severity.HARD)
    def non_empty(s: _Input) -> bool:
        return bool(s.query)

    contract: Contract[_Input, _Output] = Contract(
        name="t2",
        version="0.1.0",
        preconditions=[non_empty],
    )
    runtime: Runtime = _StubRuntime(_Output(text="never"))
    sink = MemorySink()
    with pytest.raises(PreconditionViolation):
        await run_under_contract(
            runtime=runtime,
            contract=contract,
            input=_Input(query=""),
            output_model=_Output,
            sink=sink,
        )
    kinds = [e.kind for e in sink.events]
    assert "contract_started" in kinds
    assert "precondition_violated" in kinds
    assert "contract_completed" not in kinds


@pytest.mark.asyncio
async def test_soft_postcondition_logs_and_continues() -> None:
    @predicate(name="output_long_enough", severity=Severity.SOFT)
    def output_long_enough(s: _Output) -> bool:
        return len(s.text) >= 100

    contract: Contract[_Input, _Output] = Contract(
        name="t3",
        version="0.1.0",
        postconditions=[output_long_enough],
    )
    runtime: Runtime = _StubRuntime(_Output(text="short"))
    sink = MemorySink()
    result = await run_under_contract(
        runtime=runtime,
        contract=contract,
        input=_Input(query="hi"),
        output_model=_Output,
        sink=sink,
    )
    assert isinstance(result, _Output)
    kinds = [e.kind for e in sink.events]
    assert "postcondition_violated" in kinds
    assert "contract_completed" in kinds


@pytest.mark.asyncio
async def test_hard_postcondition_halts() -> None:
    @predicate(name="output_set", severity=Severity.HARD)
    def output_set(s: _Output) -> bool:
        return bool(s.text)

    contract: Contract[_Input, _Output] = Contract(
        name="t4",
        version="0.1.0",
        postconditions=[output_set],
    )
    runtime: Runtime = _StubRuntime(_Output(text=""))
    with pytest.raises(PostconditionViolation):
        await run_under_contract(
            runtime=runtime,
            contract=contract,
            input=_Input(query="hi"),
            output_model=_Output,
        )


@pytest.mark.asyncio
async def test_hard_invariant_halts() -> None:
    @predicate(name="never_holds", severity=Severity.HARD)
    def never_holds(s: Any) -> bool:
        return False

    contract: Contract[_Input, _Output] = Contract(
        name="t5",
        version="0.1.0",
        invariants=[never_holds],
    )
    runtime: Runtime = _StubRuntime(_Output(text="x"))
    with pytest.raises(InvariantViolation):
        await run_under_contract(
            runtime=runtime,
            contract=contract,
            input=_Input(query="hi"),
            output_model=_Output,
        )


@pytest.mark.asyncio
async def test_output_parsed_from_dict() -> None:
    contract: Contract[_Input, _Output] = Contract(name="t6", version="0.1.0")
    runtime: Runtime = _StubRuntime({"text": "from-dict"})
    result = await run_under_contract(
        runtime=runtime,
        contract=contract,
        input=_Input(query="hi"),
        output_model=_Output,
    )
    assert isinstance(result, _Output)
    assert result.text == "from-dict"


@pytest.mark.asyncio
async def test_events_carry_otel_fields() -> None:
    contract: Contract[_Input, _Output] = Contract(name="t7", version="0.1.0")
    runtime: Runtime = _StubRuntime(_Output(text="x"))
    sink = MemorySink()
    await run_under_contract(
        runtime=runtime,
        contract=contract,
        input=_Input(query="hi"),
        output_model=_Output,
        sink=sink,
    )
    for e in sink.events:
        assert e.trace_id
        assert e.span_id
        assert e.workload == "t7"
        assert e.contract == "t7"
        assert e.contract_version == "0.1.0"


@pytest.mark.asyncio
async def test_all_events_share_trace_id() -> None:
    @predicate(name="logs_soft", severity=Severity.SOFT)
    def soft(s: _Input) -> bool:
        return False

    contract: Contract[_Input, _Output] = Contract(
        name="t8",
        version="0.1.0",
        preconditions=[soft],
    )
    runtime: Runtime = _StubRuntime(_Output(text="x"))
    sink = MemorySink()
    await run_under_contract(
        runtime=runtime,
        contract=contract,
        input=_Input(query="hi"),
        output_model=_Output,
        sink=sink,
    )
    trace_ids = {e.trace_id for e in sink.events}
    assert len(trace_ids) == 1
    started = [e for e in sink.events if isinstance(e, ContractStarted)]
    completed = [e for e in sink.events if isinstance(e, ContractCompleted)]
    violated = [e for e in sink.events if isinstance(e, PreconditionViolated)]
    assert len(started) == 1
    assert len(completed) == 1
    assert len(violated) == 1


@pytest.mark.asyncio
async def test_budget_passed_to_runtime() -> None:
    """When budget is provided, the runtime receives a BudgetTracker."""
    contract: Contract[_Input, _Output] = Contract(name="tb1", version="0.1.0")
    runtime = _StubRuntime(_Output(text="ok"))
    await run_under_contract(
        runtime=runtime,
        contract=contract,
        input=_Input(query="hi"),
        output_model=_Output,
        budget=ActionBudget(max_steps=10),
    )
    assert runtime.received_budget is not None
    assert runtime.received_budget.budget.max_steps == 10


@pytest.mark.asyncio
async def test_no_budget_means_no_tracker() -> None:
    """When budget is omitted, no tracker is constructed."""
    contract: Contract[_Input, _Output] = Contract(name="tb2", version="0.1.0")
    runtime = _StubRuntime(_Output(text="ok"))
    await run_under_contract(
        runtime=runtime,
        contract=contract,
        input=_Input(query="hi"),
        output_model=_Output,
    )
    assert runtime.received_budget is None


@pytest.mark.asyncio
async def test_budget_exceeded_propagates() -> None:
    """A runtime that exhausts the budget triggers BudgetExceeded."""
    contract: Contract[_Input, _Output] = Contract(name="tb3", version="0.1.0")
    runtime = _BudgetBurningRuntime()
    sink = MemorySink()
    with pytest.raises(BudgetExceeded) as exc_info:
        await run_under_contract(
            runtime=runtime,
            contract=contract,
            input=_Input(query="hi"),
            output_model=_Output,
            budget=ActionBudget(max_steps=5),
            sink=sink,
        )
    assert exc_info.value.budget_kind == "steps"
    kinds = [e.kind for e in sink.events]
    assert "budget_exceeded" in kinds


@pytest.mark.asyncio
async def test_mcp_servers_passed_to_runtime() -> None:
    """mcp_servers parameter threads through to the runtime."""
    from harness.mcp import MCPTransport

    contract: Contract[_Input, _Output] = Contract(name="tm1", version="0.1.0")
    runtime = _StubRuntime(_Output(text="ok"))
    servers = [
        MCPServerSpec(
            name="local-tool",
            transport=MCPTransport.STDIO,
            command="/bin/echo",
        )
    ]
    await run_under_contract(
        runtime=runtime,
        contract=contract,
        input=_Input(query="hi"),
        output_model=_Output,
        mcp_servers=servers,
    )
    assert runtime.received_mcp_servers == servers


@pytest.mark.asyncio
async def test_default_guard_constructed_when_contract_has_governance() -> None:
    @predicate(name="any", severity=Severity.HARD)
    def any_pred(action: Any) -> bool:
        return True

    contract: Contract[_Input, _Output] = Contract(
        name="tg1",
        version="0.1.0",
        governance=[any_pred],
    )
    runtime = _StubRuntime(_Output(text="ok"))
    await run_under_contract(
        runtime=runtime,
        contract=contract,
        input=_Input(query="hi"),
        output_model=_Output,
    )
    assert runtime.received_guard is not None


@pytest.mark.asyncio
async def test_default_guard_constructed_when_approval_required() -> None:
    contract: Contract[_Input, _Output] = Contract(
        name="tg2",
        version="0.1.0",
        approval_required=["risky"],
    )
    runtime = _StubRuntime(_Output(text="ok"))
    await run_under_contract(
        runtime=runtime,
        contract=contract,
        input=_Input(query="hi"),
        output_model=_Output,
    )
    assert runtime.received_guard is not None


class _PausingRuntime:
    """Runtime that pauses on an approval (returns a ResumableState)."""

    name: str = "pausing"

    async def run(self, prompt: str, **kw: Any) -> Any:
        from datetime import UTC, datetime

        from harness.interruption import ApprovalInterruption

        # Deliberately wrong identity/trace: the harness must overwrite.
        return ResumableState(
            contract_name="WRONG",
            contract_version="WRONG",
            workload="WRONG",
            input_payload={"adapter": "junk"},
            pending_approvals=[
                ApprovalInterruption(id="i1", created_at=datetime.now(UTC), tool="risky")
            ],
            trace_id="adapter-generated-trace",
        )

    def stream(self, prompt: str, **kw: Any) -> AsyncIterator[Any]:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_resumable_state_stamped_with_harness_identity() -> None:
    """BL-002: harness owns identity + trace_id on the paused state."""
    contract: Contract[_Input, _Output] = Contract(name="tp", version="2.3.4")
    sink = MemorySink()
    result = await run_under_contract(
        runtime=_PausingRuntime(),
        contract=contract,
        input=_Input(query="hi"),
        output_model=_Output,
        sink=sink,
    )
    assert isinstance(result, ResumableState)
    # Runtime-supplied approvals are preserved...
    assert result.pending_approvals[0].tool == "risky"
    # ...but the harness is the source of truth for identity + trace.
    assert result.contract_name == "tp"
    assert result.contract_version == "2.3.4"
    assert result.workload == "tp"
    assert result.input_payload == {"query": "hi"}
    started = next(e for e in sink.events if e.kind == "contract_started")
    assert result.trace_id == started.trace_id


@pytest.mark.asyncio
async def test_no_guard_when_contract_has_no_governance() -> None:
    contract: Contract[_Input, _Output] = Contract(name="tg3", version="0.1.0")
    runtime = _StubRuntime(_Output(text="ok"))
    await run_under_contract(
        runtime=runtime,
        contract=contract,
        input=_Input(query="hi"),
        output_model=_Output,
    )
    assert runtime.received_guard is None
