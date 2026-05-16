"""Tests for harness.interruption."""

from __future__ import annotations

from datetime import UTC, datetime

from harness.interruption import (
    ApprovalInterruption,
    ResumableState,
)


def _interruption(id: str = "i-1", tool: str = "delete_user") -> ApprovalInterruption:
    return ApprovalInterruption(
        id=id,
        created_at=datetime.now(UTC),
        tool=tool,
    )


def _state(pending: list[ApprovalInterruption]) -> ResumableState:
    return ResumableState(
        contract_name="example",
        contract_version="0.1.0",
        workload="example",
        input_payload={"query": "q"},
        pending_approvals=pending,
        trace_id="trace-1",
    )


def test_approval_interruption_defaults_pending() -> None:
    ai = _interruption()
    assert ai.decision == "pending"
    assert ai.decision_reason is None


def test_state_is_frozen() -> None:
    s = _state([])
    try:
        s.workload = "other"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("state should be frozen")


def test_approve_produces_new_state_with_approved_interruption() -> None:
    ai = _interruption(id="i-1")
    s1 = _state([ai])
    s2 = s1.approve("i-1")
    assert s1 is not s2  # new instance
    assert s1.pending_approvals[0].decision == "pending"  # original unchanged
    assert s2.pending_approvals[0].decision == "approved"


def test_deny_with_reason() -> None:
    ai = _interruption(id="i-2", tool="rm -rf /")
    s1 = _state([ai])
    s2 = s1.deny("i-2", reason="too destructive")
    assert s2.pending_approvals[0].decision == "denied"
    assert s2.pending_approvals[0].decision_reason == "too destructive"


def test_approve_only_affects_matching_id() -> None:
    a = _interruption(id="a")
    b = _interruption(id="b")
    s = _state([a, b]).approve("a")
    decisions = {ai.id: ai.decision for ai in s.pending_approvals}
    assert decisions == {"a": "approved", "b": "pending"}
