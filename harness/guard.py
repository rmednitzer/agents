"""Tool guard surface for per-action policy enforcement.

The Runtime adapter invokes a ToolGuard before each proposed tool call.
The guard returns one of:

- APPROVE: runtime proceeds with the call.
- REJECT: runtime aborts the call. On HARD severity, the harness raises
  GovernanceViolation; on SOFT severity, the runtime is expected to log
  and continue (the guard already emitted the violation event).
- REQUIRE_APPROVAL: runtime captures the proposal and the harness produces
  a ResumableState for human-in-the-loop approval.

HarnessToolGuard is the default implementation; it consults the Contract's
governance predicates and approval_required list.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from harness.contract import Contract, Severity
from harness.events import ApprovalRequested, GovernanceViolated
from harness.sinks import EventSink, NullSink

__all__ = [
    "GuardDecision",
    "GuardResponse",
    "HarnessToolGuard",
    "ProposedAction",
    "ToolGuard",
]


@dataclass(frozen=True)
class ProposedAction:
    """A proposed tool call. Passed to governance predicates."""

    tool: str
    arguments: dict[str, Any]


class GuardDecision(StrEnum):
    """Outcome of a ToolGuard check."""

    APPROVE = "approve"
    REJECT = "reject"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class GuardResponse:
    """Result returned by a ToolGuard.

    Attributes:
        decision: APPROVE, REJECT, or REQUIRE_APPROVAL.
        reason: Optional explanation; populated on REJECT.
        severity: For REJECT, indicates whether the harness should raise
            (HARD) or the runtime should log and continue (SOFT).
        interruption_id: For REQUIRE_APPROVAL, the id of the
            ApprovalInterruption that was emitted; runtime captures this
            in the ResumableState.
    """

    decision: GuardDecision
    reason: str | None = None
    severity: Severity = Severity.HARD
    interruption_id: str | None = None


@runtime_checkable
class ToolGuard(Protocol):
    """Runtime-side hook invoked before each proposed tool call."""

    async def check(self, tool: str, arguments: dict[str, Any]) -> GuardResponse: ...


class HarnessToolGuard:
    """Default ToolGuard. Enforces a Contract's governance + approval_required.

    Construction binds the guard to a contract and an event sink plus the
    base event fields (workload, contract, trace_id, span_id, version) so
    emissions are correlated with the surrounding run.
    """

    def __init__(
        self,
        contract: Contract[Any, Any],
        *,
        sink: EventSink | None = None,
        base_event_fields: dict[str, Any] | None = None,
    ) -> None:
        self._contract = contract
        self._sink: EventSink = sink if sink is not None else NullSink()
        self._base = base_event_fields if base_event_fields is not None else {}

    async def check(self, tool: str, arguments: dict[str, Any]) -> GuardResponse:
        action = ProposedAction(tool=tool, arguments=arguments)

        # 1. Governance predicates fire in declaration order
        for pred in self._contract.governance:
            if not pred(action):
                if self._base:
                    self._sink.emit(
                        GovernanceViolated(
                            timestamp=datetime.now(UTC),
                            predicate=pred.name,
                            severity=pred.severity,
                            action=tool,
                            action_arguments=arguments,
                            **self._base,
                        )
                    )
                if pred.severity == Severity.HARD:
                    return GuardResponse(
                        decision=GuardDecision.REJECT,
                        reason=f"governance predicate '{pred.name}' failed",
                        severity=Severity.HARD,
                    )
                # SOFT: log and continue to next predicate

        # 2. Approval requirement
        if tool in self._contract.approval_required:
            interruption_id = uuid.uuid4().hex
            if self._base:
                self._sink.emit(
                    ApprovalRequested(
                        timestamp=datetime.now(UTC),
                        interruption_id=interruption_id,
                        tool=tool,
                        arguments=arguments,
                        **self._base,
                    )
                )
            return GuardResponse(
                decision=GuardDecision.REQUIRE_APPROVAL,
                interruption_id=interruption_id,
            )

        return GuardResponse(decision=GuardDecision.APPROVE)
