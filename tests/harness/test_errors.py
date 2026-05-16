"""Tests for harness.errors."""

from __future__ import annotations

import pytest

from harness.errors import (
    ApprovalDenied,
    BudgetExceeded,
    GovernanceViolation,
    HarnessError,
    InvariantViolation,
    PostconditionViolation,
    PreconditionViolation,
)


def test_all_errors_inherit_from_harness_error() -> None:
    for exc_cls in (
        PreconditionViolation,
        PostconditionViolation,
        InvariantViolation,
        GovernanceViolation,
        BudgetExceeded,
        ApprovalDenied,
    ):
        assert issubclass(exc_cls, HarnessError)


def test_precondition_carries_attributes() -> None:
    err = PreconditionViolation("input_non_empty", "got empty string")
    assert err.predicate == "input_non_empty"
    assert err.reason == "got empty string"
    assert "input_non_empty" in str(err)
    assert "got empty string" in str(err)


def test_budget_exceeded_carries_attributes() -> None:
    err = BudgetExceeded("steps", limit=10.0, consumed=11.0)
    assert err.budget_kind == "steps"
    assert err.limit == 10.0
    assert err.consumed == 11.0
    assert "11" in str(err)


def test_approval_denied_optional_reason() -> None:
    err = ApprovalDenied("delete_user")
    assert err.tool == "delete_user"
    assert err.reason is None


def test_raise_and_catch() -> None:
    with pytest.raises(HarnessError):
        raise InvariantViolation("memory_ns_isolation")
