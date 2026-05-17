"""run_under_contract record_sink wiring (BL-185, ADR 0012)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from harness.contract import Contract, Severity, predicate
from harness.enforcement import run_under_contract
from harness.errors import BudgetExceeded, PreconditionViolation
from harness.interruption import ResumableState
from harness.provenance import RunRecord, contract_digest, verify_run_record


class _In(BaseModel):
    q: str


class _Out(BaseModel):
    t: str


class _Runtime:
    name = "stub"

    def __init__(self, behaviour: str) -> None:
        self._behaviour = behaviour

    async def run(self, prompt: str, **kw: Any) -> Any:
        if self._behaviour == "ok":
            return _Out(t="done")
        if self._behaviour == "budget":
            raise BudgetExceeded("tokens", 10.0, 11.0)
        if self._behaviour == "pause":
            return ResumableState(
                contract_name="",
                contract_version="",
                workload="",
                input_payload={},
                pending_approvals=[],
                trace_id="t",
            )
        raise AssertionError(self._behaviour)

    def stream(self, prompt: str, **kw: Any) -> AsyncIterator[Any]:
        raise NotImplementedError


@predicate(name="non_empty", severity=Severity.HARD)
def _non_empty(s: _In) -> bool:
    return bool(s.q)


def _contract() -> Contract[_In, _Out]:
    return Contract(name="wl", version="1.0.0", preconditions=[_non_empty])


async def test_completed_emits_verifiable_record() -> None:
    records: list[RunRecord] = []
    c = _contract()
    out = await run_under_contract(_Runtime("ok"), c, _In(q="x"), _Out, record_sink=records.append)
    assert out == _Out(t="done")
    assert len(records) == 1
    rec = records[0]
    assert rec.outcome == "completed"
    assert rec.contract_digest == contract_digest(c)
    assert verify_run_record(rec, c) == []


async def test_precondition_violation_emits_record_then_raises() -> None:
    records: list[RunRecord] = []
    c = _contract()
    with pytest.raises(PreconditionViolation):
        await run_under_contract(_Runtime("ok"), c, _In(q=""), _Out, record_sink=records.append)
    assert [r.outcome for r in records] == ["precondition"]


async def test_runtime_budget_exception_maps_to_budget_outcome() -> None:
    records: list[RunRecord] = []
    with pytest.raises(BudgetExceeded):
        await run_under_contract(
            _Runtime("budget"), _contract(), _In(q="x"), _Out, record_sink=records.append
        )
    assert [r.outcome for r in records] == ["budget"]


async def test_pause_emits_paused_record() -> None:
    records: list[RunRecord] = []
    result = await run_under_contract(
        _Runtime("pause"), _contract(), _In(q="x"), _Out, record_sink=records.append
    )
    assert isinstance(result, ResumableState)
    assert [r.outcome for r in records] == ["paused"]


async def test_no_record_sink_is_a_noop() -> None:
    out = await run_under_contract(_Runtime("ok"), _contract(), _In(q="x"), _Out)
    assert out == _Out(t="done")
