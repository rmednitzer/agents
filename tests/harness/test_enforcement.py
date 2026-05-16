"""Tests for harness.enforcement."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from harness.contract import Contract, Severity, predicate
from harness.enforcement import run_under_contract
from harness.errors import (
    InvariantViolation,
    PostconditionViolation,
    PreconditionViolation,
)
from harness.events import (
    ContractCompleted,
    ContractStarted,
    PreconditionViolated,
)
from harness.runtime import Runtime
from harness.sinks import MemorySink


class _Input(BaseModel):
    query: str


class _Output(BaseModel):
    text: str


class _StubRuntime:
    """Test runtime that returns a fixed Output instance."""

    name: str = "stub"

    def __init__(self, output: _Output | dict[str, Any]) -> None:
        self._output = output

    async def run(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        max_steps: int | None = None,
    ) -> Any:
        return self._output

    def stream(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        max_steps: int | None = None,
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
