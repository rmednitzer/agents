"""Exception hierarchy for the harness.

All harness exceptions inherit from HarnessError. Hard contract violations
raise specific subtypes so callers can distinguish them. Soft violations do
not raise; they emit a Violation event and the run continues.
"""

from __future__ import annotations

__all__ = [
    "ApprovalDenied",
    "BudgetExceeded",
    "GovernanceViolation",
    "HarnessError",
    "InvariantViolation",
    "PostconditionViolation",
    "PreconditionViolation",
]


class HarnessError(Exception):
    """Base for all harness exceptions."""


class PreconditionViolation(HarnessError):
    """A hard precondition predicate failed before the workload ran."""

    def __init__(self, predicate: str, reason: str | None = None) -> None:
        suffix = f": {reason}" if reason else ""
        super().__init__(f"Precondition '{predicate}' violated{suffix}")
        self.predicate = predicate
        self.reason = reason


class PostconditionViolation(HarnessError):
    """A hard postcondition predicate failed after the workload ran."""

    def __init__(self, predicate: str, reason: str | None = None) -> None:
        suffix = f": {reason}" if reason else ""
        super().__init__(f"Postcondition '{predicate}' violated{suffix}")
        self.predicate = predicate
        self.reason = reason


class InvariantViolation(HarnessError):
    """A hard invariant predicate failed during the run."""

    def __init__(self, predicate: str, reason: str | None = None) -> None:
        suffix = f": {reason}" if reason else ""
        super().__init__(f"Invariant '{predicate}' violated{suffix}")
        self.predicate = predicate
        self.reason = reason


class GovernanceViolation(HarnessError):
    """A hard governance predicate failed for a proposed action."""

    def __init__(self, predicate: str, action: str | None = None) -> None:
        suffix = f" on action '{action}'" if action else ""
        super().__init__(f"Governance predicate '{predicate}' violated{suffix}")
        self.predicate = predicate
        self.action = action


class BudgetExceeded(HarnessError):
    """An action budget was exceeded during the run."""

    def __init__(self, budget_kind: str, limit: float, consumed: float) -> None:
        super().__init__(f"Budget '{budget_kind}' exceeded: limit={limit} consumed={consumed}")
        self.budget_kind = budget_kind
        self.limit = limit
        self.consumed = consumed


class ApprovalDenied(HarnessError):
    """A human-in-the-loop approval was denied for a proposed tool call."""

    def __init__(self, tool: str, reason: str | None = None) -> None:
        suffix = f": {reason}" if reason else ""
        super().__init__(f"Approval denied for tool '{tool}'{suffix}")
        self.tool = tool
        self.reason = reason
