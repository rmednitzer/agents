"""Regression tests for the ADR 0011 audit follow-ups (harness).

- BL-174: ``compose_contracts`` keeps the strictest governance
  predicate on a name collision (a HARD veto must not be downgraded to
  SOFT by an earlier same-named soft declaration).
- BL-175: a postcondition retry (BL-102) records each predicate into
  the DriftMonitor exactly once, not once per leg.
- BL-176: ``run_under_contract`` stamps ``parent_span_id`` on every
  emitted event when given; None preserves the prior behaviour.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from harness.composition import compose_contracts
from harness.contract import Contract, Severity, predicate
from harness.drift import DriftMonitor
from harness.enforcement import run_under_contract
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


# --- BL-174: governance composition keeps the strictest --------------


@predicate(name="delete_guard", severity=Severity.SOFT)
def _guard_soft(action: object) -> bool:
    return True


@predicate(name="delete_guard", severity=Severity.HARD)
def _guard_hard(action: object) -> bool:
    return True


def test_governance_union_keeps_hard_over_earlier_soft() -> None:
    """A workload's SOFT governance predicate declared first must not
    downgrade a skill's same-named HARD veto (the governance analogue
    of BL-166)."""
    workload: Contract[Any, Any] = Contract(name="w", version="1", governance=[_guard_soft])
    skill: Contract[Any, Any] = Contract(name="s", version="1", governance=[_guard_hard])
    composed = compose_contracts("c", "1", workload, skill)
    govs = {g.name: g for g in composed.governance}
    assert govs["delete_guard"].severity == Severity.HARD


def test_governance_union_order_and_dedup_preserved() -> None:
    """Strictest does not change dedup-by-name or first-declared order."""

    @predicate(name="a", severity=Severity.HARD)
    def _a(x: object) -> bool:
        return True

    @predicate(name="b", severity=Severity.SOFT)
    def _b(x: object) -> bool:
        return True

    c1: Contract[Any, Any] = Contract(name="c1", version="1", governance=[_a, _b])
    c2: Contract[Any, Any] = Contract(name="c2", version="1", governance=[_b])
    composed = compose_contracts("c", "1", c1, c2)
    assert [g.name for g in composed.governance] == ["a", "b"]


# --- BL-175: postcondition retry records drift once ------------------


@predicate(name="post", severity=Severity.SOFT)
def _post_fixed(o: _Out) -> bool:
    return o.text == "fixed"


class _Retry:
    async def recover(self, *, predicate: str, stage: str, state: Any) -> RecoveryOutcome:
        return RecoveryOutcome(action="retry", directive="retry")


@pytest.mark.asyncio
async def test_postcondition_retry_records_drift_once() -> None:
    """One baseline pass then a failing run whose retry directive fires:
    the failing postcondition is recorded once (fixed) not twice
    (buggy), so the distribution is an even pass/fail split."""
    monitor = DriftMonitor()
    contract: Contract[_In, _Out] = Contract(name="c", version="1", postconditions=[_post_fixed])
    # Baseline: one passing run -> counts {pass: 1}.
    await run_under_contract(
        runtime=_Stub(_Out(text="fixed")),
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        drift_monitor=monitor,
    )
    # A failing run; the retry directive re-invokes once, still failing.
    stub = _Stub(_Out(text="still-bad"))
    await run_under_contract(
        runtime=stub,
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        recovery={"post": _Retry()},
        drift_monitor=monitor,
    )
    assert stub.runs == 2  # a retry did happen (the double-record path)
    # Exactly one "fail" recorded for the retried run, not two.
    assert monitor.distribution("post") == {"pass": 0.5, "fail": 0.5}


# --- BL-176: parent_span_id propagation ------------------------------


@pytest.mark.asyncio
async def test_parent_span_id_is_stamped_on_events() -> None:
    sink = MemorySink()
    contract: Contract[_In, _Out] = Contract(name="c", version="1")
    await run_under_contract(
        runtime=_Stub(),
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        sink=sink,
        parent_span_id="parent-123",
    )
    assert sink.events
    assert all(e.parent_span_id == "parent-123" for e in sink.events)


@pytest.mark.asyncio
async def test_parent_span_id_default_is_none() -> None:
    sink = MemorySink()
    contract: Contract[_In, _Out] = Contract(name="c", version="1")
    await run_under_contract(
        runtime=_Stub(),
        contract=contract,
        input=_In(query="q"),
        output_model=_Out,
        sink=sink,
    )
    assert sink.events
    assert all(e.parent_span_id is None for e in sink.events)
