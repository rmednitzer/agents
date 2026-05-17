"""L3 default-path wiring + audit fixes through run_under_contract.

Covers BL-100 (skill-contract composition), BL-101 (drift recording +
threshold event), BL-102 (recovery directives), BL-104 (run-scoped
lifecycles), BL-154 (cumulative budgets across resume), and the audit
fix A2 (a raising recovery handler does not halt a soft path).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from harness.budgets import ActionBudget
from harness.contract import Contract, Severity, predicate
from harness.drift import DriftMonitor
from harness.enforcement import run_under_contract
from harness.errors import PostconditionViolation
from harness.interruption import ApprovalInterruption, ResumableState
from harness.recovery import RecoveryOutcome
from harness.sinks import MemorySink


class _In(BaseModel):
    query: str


class _Out(BaseModel):
    text: str


class _Stub:
    name = "stub"

    def __init__(self, out: _Out | None = None) -> None:
        self._out = out or _Out(text="ok")
        self.runs = 0

    async def run(self, prompt: str, **kw: Any) -> Any:
        self.runs += 1
        return self._out

    def stream(self, prompt: str, **kw: Any) -> AsyncIterator[Any]:
        raise NotImplementedError


# --- BL-100: skill-contract composition ------------------------------


@predicate(name="shared_post", severity=Severity.HARD)
def _shared_post(o: _Out) -> bool:
    return o.text == "ok"


@predicate(name="skill_only_post", severity=Severity.HARD)
def _skill_only_post(o: _Out) -> bool:
    return False  # would fail if it survived composition


@pytest.mark.asyncio
async def test_bl100_skill_contracts_compose_by_intersection() -> None:
    workload: Contract[_In, _Out] = Contract(name="w", version="1", postconditions=[_shared_post])
    skill: Contract[Any, Any] = Contract(
        name="s", version="1", postconditions=[_shared_post, _skill_only_post]
    )
    # Intersection: skill_only_post is dropped (not in the workload), so
    # the run completes; only the shared obligation is enforced.
    result = await run_under_contract(
        runtime=_Stub(),
        contract=workload,
        input=_In(query="q"),
        output_model=_Out,
        skill_contracts=[skill],
    )
    assert isinstance(result, _Out)


@pytest.mark.asyncio
async def test_bl100_omitted_is_l1() -> None:
    workload: Contract[_In, _Out] = Contract(name="w", version="1", postconditions=[_shared_post])
    result = await run_under_contract(
        runtime=_Stub(),
        contract=workload,
        input=_In(query="q"),
        output_model=_Out,
    )
    assert isinstance(result, _Out)


# --- BL-101: drift recording + threshold event -----------------------


@predicate(name="post_ok", severity=Severity.SOFT)
def _post_ok(o: _Out) -> bool:
    return o.text == "ok"


@pytest.mark.asyncio
async def test_bl101_drift_records_and_alerts() -> None:
    monitor = DriftMonitor()
    contract: Contract[_In, _Out] = Contract(name="c", version="1", postconditions=[_post_ok])
    # Baseline: three passing runs, snapshot the reference.
    for _ in range(3):
        await run_under_contract(
            runtime=_Stub(_Out(text="ok")),
            contract=contract,
            input=_In(query="q"),
            output_model=_Out,
            drift_monitor=monitor,
        )
    monitor.snapshot_reference("post_ok")
    sink = MemorySink()
    # A failing run shifts the distribution past the threshold.
    await run_under_contract(
        runtime=_Stub(_Out(text="bad")),
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        sink=sink,
        drift_monitor=monitor,
        drift_threshold=0.05,
    )
    crossed = [e for e in sink.events if e.kind == "drift_threshold_crossed"]
    assert crossed
    assert crossed[0].predicate == "post_ok"
    assert crossed[0].divergence > 0.05


@pytest.mark.asyncio
async def test_bl101_no_threshold_is_silent() -> None:
    monitor = DriftMonitor()
    contract: Contract[_In, _Out] = Contract(name="c", version="1", postconditions=[_post_ok])
    sink = MemorySink()
    await run_under_contract(
        runtime=_Stub(),
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        sink=sink,
        drift_monitor=monitor,
    )
    assert not [e for e in sink.events if e.kind == "drift_threshold_crossed"]
    assert monitor.distribution("post_ok") == {"pass": 1.0}


# --- BL-102: recovery directives -------------------------------------


@predicate(name="needs_fix", severity=Severity.SOFT)
def _needs_fix(o: _Out) -> bool:
    return o.text == "fixed"


class _Directive:
    def __init__(self, outcome: RecoveryOutcome) -> None:
        self._outcome = outcome

    async def recover(self, *, predicate: str, stage: str, state: Any) -> RecoveryOutcome:
        return self._outcome


@pytest.mark.asyncio
async def test_bl102_substitute_replaces_output() -> None:
    contract: Contract[_In, _Out] = Contract(name="c", version="1", postconditions=[_needs_fix])
    handler = _Directive(
        RecoveryOutcome(
            action="substituted",
            directive="substitute",
            replacement=_Out(text="fixed"),
        )
    )
    result = await run_under_contract(
        runtime=_Stub(_Out(text="raw")),
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        recovery={"needs_fix": handler},
    )
    assert isinstance(result, _Out)
    assert result.text == "fixed"


@pytest.mark.asyncio
async def test_bl102_escalate_raises() -> None:
    contract: Contract[_In, _Out] = Contract(name="c", version="1", postconditions=[_needs_fix])
    handler = _Directive(RecoveryOutcome(action="escalate", directive="escalate"))
    with pytest.raises(PostconditionViolation):
        await run_under_contract(
            runtime=_Stub(_Out(text="raw")),
            contract=contract,
            input=_In(query="q"),
            output_model=_Out,
            recovery={"needs_fix": handler},
        )


@pytest.mark.asyncio
async def test_bl102_retry_reinvokes_once() -> None:
    contract: Contract[_In, _Out] = Contract(name="c", version="1", postconditions=[_needs_fix])
    handler = _Directive(RecoveryOutcome(action="retry", directive="retry"))
    stub = _Stub(_Out(text="still-bad"))
    result = await run_under_contract(
        runtime=stub,
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        recovery={"needs_fix": handler},
    )
    # One retry, then soft-continue with the (still failing) output.
    assert stub.runs == 2
    assert isinstance(result, _Out)


# --- A2: a raising recovery handler does not halt a soft path --------


class _Raising:
    async def recover(self, *, predicate: str, stage: str, state: Any) -> RecoveryOutcome:
        raise RuntimeError("handler boom")


@pytest.mark.asyncio
async def test_a2_raising_handler_is_contained() -> None:
    contract: Contract[_In, _Out] = Contract(name="c", version="1", postconditions=[_needs_fix])
    sink = MemorySink()
    result = await run_under_contract(
        runtime=_Stub(_Out(text="raw")),
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        sink=sink,
        recovery={"needs_fix": _Raising()},
    )
    assert isinstance(result, _Out)  # soft never halts
    applied = [e for e in sink.events if e.kind == "recovery_applied"]
    assert applied
    assert applied[0].recovered is False
    assert "boom" in applied[0].action


# --- BL-104: run-scoped lifecycles -----------------------------------


class _Lifecycle:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _Lifecycle:
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.exited = True


@pytest.mark.asyncio
async def test_bl104_lifecycle_started_and_stopped() -> None:
    lc = _Lifecycle()
    contract: Contract[_In, _Out] = Contract(name="c", version="1")
    await run_under_contract(
        runtime=_Stub(),
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        lifecycles=[lc],
    )
    assert lc.entered
    assert lc.exited


# --- BL-154: cumulative budgets across an approval pause -------------


class _PausingRuntime:
    """Returns a ResumableState first, then completes on resume."""

    name = "pausing"

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, prompt: str, *, budget: Any = None, resume: Any = None, **kw: Any) -> Any:
        self.calls += 1
        if resume is None:
            # Simulate the adapter having consumed some budget pre-pause.
            if budget is not None:
                budget.consume_tokens(40)
            return ResumableState(
                contract_name="x",
                contract_version="x",
                workload="x",
                input_payload={},
                pending_approvals=[ApprovalInterruption(id="a1", created_at=_now(), tool="t")],
                trace_id="t",
            )
        if budget is not None:
            budget.consume_tokens(40)
        return _Out(text="ok")

    def stream(self, prompt: str, **kw: Any) -> AsyncIterator[Any]:
        raise NotImplementedError


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)


@pytest.mark.asyncio
async def test_bl154_budget_accumulates_across_resume() -> None:
    contract: Contract[_In, _Out] = Contract(name="c", version="1")
    rt = _PausingRuntime()
    budget = ActionBudget(max_tokens=70)

    paused = await run_under_contract(
        runtime=rt,
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        budget=budget,
    )
    assert isinstance(paused, ResumableState)
    # Snapshot threaded onto the state: 40 tokens consumed pre-pause.
    assert paused.consumed_tokens == 40

    resumed = paused.approve("a1")
    # On resume the tracker is seeded with 40; the second leg adds 40,
    # crossing the 70 cap, proving accumulation (not a reset to zero).
    from harness.errors import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        await run_under_contract(
            runtime=rt,
            contract=contract,
            input=_In(query="q"),
            output_model=_Out,
            budget=budget,
            resume=resumed,
        )
