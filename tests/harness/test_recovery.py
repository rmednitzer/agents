"""Tests for soft-violation recovery handlers (BL-061)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from harness.contract import Contract, Severity, predicate
from harness.enforcement import run_under_contract
from harness.errors import PreconditionViolation
from harness.recovery import RecoveryOutcome
from harness.sinks import MemorySink


class _In(BaseModel):
    query: str


class _Out(BaseModel):
    text: str


class _Stub:
    name = "stub"

    async def run(self, prompt: str, **kw: Any) -> Any:
        return _Out(text="ok")

    def stream(self, prompt: str, **kw: Any) -> AsyncIterator[Any]:
        raise NotImplementedError


class _RecordingHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def recover(self, *, predicate: str, stage: str, state: Any) -> RecoveryOutcome:
        self.calls.append((predicate, stage))
        return RecoveryOutcome(action="substituted default", recovered=True)


@predicate(name="soft_pre", severity=Severity.SOFT)
def _soft_pre(s: _In) -> bool:
    return False


@predicate(name="hard_pre", severity=Severity.HARD)
def _hard_pre(s: _In) -> bool:
    return False


@pytest.mark.asyncio
async def test_recovery_runs_on_soft_and_emits_event() -> None:
    contract: Contract[_In, _Out] = Contract(name="c", version="1", preconditions=[_soft_pre])
    handler = _RecordingHandler()
    sink = MemorySink()
    result = await run_under_contract(
        runtime=_Stub(),
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        sink=sink,
        recovery={"soft_pre": handler},
    )
    assert isinstance(result, _Out)  # soft -> run still completes
    assert handler.calls == [("soft_pre", "precondition")]
    applied = [e for e in sink.events if e.kind == "recovery_applied"]
    assert len(applied) == 1
    assert applied[0].action == "substituted default"
    assert applied[0].recovered is True


@pytest.mark.asyncio
async def test_no_recovery_map_is_l1_behavior() -> None:
    contract: Contract[_In, _Out] = Contract(name="c", version="1", preconditions=[_soft_pre])
    sink = MemorySink()
    await run_under_contract(
        runtime=_Stub(),
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        sink=sink,
    )
    assert not [e for e in sink.events if e.kind == "recovery_applied"]


@pytest.mark.asyncio
async def test_hard_violation_not_recovered() -> None:
    contract: Contract[_In, _Out] = Contract(name="c", version="1", preconditions=[_hard_pre])
    handler = _RecordingHandler()
    with pytest.raises(PreconditionViolation):
        await run_under_contract(
            runtime=_Stub(),
            contract=contract,
            input=_In(query="q"),
            output_model=_Out,
            recovery={"hard_pre": handler},
        )
    assert handler.calls == []  # hard halts before recovery
