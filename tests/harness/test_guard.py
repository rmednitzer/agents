"""Tests for harness.guard."""

from __future__ import annotations

import pytest

from harness.contract import Contract, Severity, predicate
from harness.events import ApprovalRequested, GovernanceViolated
from harness.guard import (
    GuardDecision,
    HarnessToolGuard,
    ProposedAction,
)
from harness.sinks import MemorySink


def _base() -> dict[str, str]:
    return {
        "workload": "w",
        "contract": "c",
        "contract_version": "0.1.0",
        "trace_id": "trace-1",
        "span_id": "span-1",
    }


@pytest.mark.asyncio
async def test_empty_contract_approves_everything() -> None:
    contract: Contract[None, None] = Contract(name="c", version="0.1.0")
    guard = HarnessToolGuard(contract)
    response = await guard.check("any_tool", {"x": 1})
    assert response.decision == GuardDecision.APPROVE


@pytest.mark.asyncio
async def test_hard_governance_failure_rejects_and_emits() -> None:
    @predicate(name="no_rm_rf", severity=Severity.HARD)
    def no_rm_rf(action: ProposedAction) -> bool:
        return not (action.tool == "shell" and "rm -rf /" in str(action.arguments))

    contract: Contract[None, None] = Contract(name="c", version="0.1.0", governance=[no_rm_rf])
    sink = MemorySink()
    guard = HarnessToolGuard(contract, sink=sink, base_event_fields=_base())

    response = await guard.check("shell", {"cmd": "rm -rf /"})
    assert response.decision == GuardDecision.REJECT
    assert response.severity == Severity.HARD
    assert response.reason is not None
    assert "no_rm_rf" in response.reason
    assert len(sink.events) == 1
    assert isinstance(sink.events[0], GovernanceViolated)


@pytest.mark.asyncio
async def test_soft_governance_failure_rejects_softly() -> None:
    """A soft governance failure is a SOFT REJECT, not APPROVE.

    The runtime logs-and-continues on a SOFT reject (it surfaces a
    structured rejection to the model rather than executing the tool).
    Returning APPROVE here, the pre-audit behaviour, made a soft
    governance predicate a silent no-op and left the runtime's
    documented soft-reject path dead (audit A1).
    """

    @predicate(name="prefer_dry_run", severity=Severity.SOFT)
    def prefer_dry_run(action: ProposedAction) -> bool:
        return action.arguments.get("dry_run", False)

    contract: Contract[None, None] = Contract(
        name="c", version="0.1.0", governance=[prefer_dry_run]
    )
    sink = MemorySink()
    guard = HarnessToolGuard(contract, sink=sink, base_event_fields=_base())

    response = await guard.check("apply_change", {"dry_run": False})
    assert response.decision == GuardDecision.REJECT
    assert response.severity == Severity.SOFT
    assert response.reason is not None
    assert "prefer_dry_run" in response.reason
    assert len(sink.events) == 1
    assert isinstance(sink.events[0], GovernanceViolated)
    assert sink.events[0].severity == Severity.SOFT


@pytest.mark.asyncio
async def test_soft_then_hard_governance_still_hard_rejects() -> None:
    """A HARD failure short-circuits even if a SOFT one preceded it."""

    @predicate(name="soft_pred", severity=Severity.SOFT)
    def soft_pred(action: ProposedAction) -> bool:
        return False

    @predicate(name="hard_pred", severity=Severity.HARD)
    def hard_pred(action: ProposedAction) -> bool:
        return False

    contract: Contract[None, None] = Contract(
        name="c", version="0.1.0", governance=[soft_pred, hard_pred]
    )
    guard = HarnessToolGuard(contract)
    response = await guard.check("t", {})
    assert response.decision == GuardDecision.REJECT
    assert response.severity == Severity.HARD
    assert response.reason is not None
    assert "hard_pred" in response.reason


@pytest.mark.asyncio
async def test_approval_required_returns_require_approval() -> None:
    contract: Contract[None, None] = Contract(
        name="c",
        version="0.1.0",
        approval_required=["delete_user"],
    )
    sink = MemorySink()
    guard = HarnessToolGuard(contract, sink=sink, base_event_fields=_base())

    response = await guard.check("delete_user", {"id": 42})
    assert response.decision == GuardDecision.REQUIRE_APPROVAL
    assert response.interruption_id is not None
    assert len(sink.events) == 1
    assert isinstance(sink.events[0], ApprovalRequested)
    assert sink.events[0].tool == "delete_user"
    assert sink.events[0].interruption_id == response.interruption_id


@pytest.mark.asyncio
async def test_non_required_tool_approves() -> None:
    contract: Contract[None, None] = Contract(
        name="c",
        version="0.1.0",
        approval_required=["delete_user"],
    )
    guard = HarnessToolGuard(contract)
    response = await guard.check("read_file", {"path": "/tmp/x"})
    assert response.decision == GuardDecision.APPROVE


@pytest.mark.asyncio
async def test_governance_runs_before_approval_check() -> None:
    @predicate(name="blocked", severity=Severity.HARD)
    def blocked(action: ProposedAction) -> bool:
        return False

    contract: Contract[None, None] = Contract(
        name="c",
        version="0.1.0",
        governance=[blocked],
        approval_required=["delete_user"],
    )
    sink = MemorySink()
    guard = HarnessToolGuard(contract, sink=sink, base_event_fields=_base())

    response = await guard.check("delete_user", {"id": 42})
    assert response.decision == GuardDecision.REJECT
    assert len(sink.events) == 1
    assert isinstance(sink.events[0], GovernanceViolated)


def test_proposed_action_is_frozen() -> None:
    a = ProposedAction(tool="x", arguments={"k": "v"})
    try:
        a.tool = "y"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ProposedAction should be frozen")
