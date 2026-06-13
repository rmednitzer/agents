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

from harness.authority import AuthorityTier, TierClassifier
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
        tier: The AuthorityTier an optional TierClassifier assigned to
            this action (BL-242), or None when no classifier is
            configured. Annotates APPROVE (so a Tier 1 action can be
            logged or notified) and REQUIRE_APPROVAL (so an approver
            sees the blast radius); never set on REJECT.
    """

    decision: GuardDecision
    reason: str | None = None
    severity: Severity = Severity.HARD
    interruption_id: str | None = None
    tier: AuthorityTier | None = None


@runtime_checkable
class ToolGuard(Protocol):
    """Runtime-side hook invoked before each proposed tool call."""

    async def check(self, tool: str, arguments: dict[str, Any]) -> GuardResponse: ...


class HarnessToolGuard:
    """Default ToolGuard. Enforces a Contract's governance + approval_required.

    Construction binds the guard to a contract and an event sink plus the
    base event fields (workload, contract, trace_id, span_id, version) so
    emissions are correlated with the surrounding run. When a
    ``TierClassifier`` is supplied, the guard also enforces blast-radius
    authority tiers (BL-242): an action classified at ``approval_tier`` or
    above is escalated to REQUIRE_APPROVAL beyond the static
    ``approval_required`` list.
    """

    def __init__(
        self,
        contract: Contract[Any, Any],
        *,
        sink: EventSink | None = None,
        base_event_fields: dict[str, Any] | None = None,
        tier_classifier: TierClassifier | None = None,
        approval_tier: AuthorityTier = AuthorityTier.STATEFUL,
    ) -> None:
        self._contract = contract
        self._sink: EventSink = sink if sink is not None else NullSink()
        self._base = base_event_fields if base_event_fields is not None else {}
        # BL-242: an optional blast-radius classifier. When set, the guard
        # escalates a Tier ``approval_tier``-or-above action to
        # REQUIRE_APPROVAL beyond the static approval_required list; None
        # preserves the L1 flat behaviour.
        self._tier_classifier = tier_classifier
        self._approval_tier = approval_tier

    async def check(self, tool: str, arguments: dict[str, Any]) -> GuardResponse:
        action = ProposedAction(tool=tool, arguments=arguments)

        # 1. Governance predicates fire in declaration order. A HARD
        # failure short-circuits to REJECT. SOFT failures are collected:
        # the run continues, but the model must still be told the call
        # was governed away, so the first soft failure becomes a
        # REJECT/SOFT response (the runtime logs-and-continues and
        # surfaces a structured rejection rather than the tool output).
        # Without this a soft governance predicate executed the tool
        # anyway and the runtime's documented soft-reject path was dead.
        soft_failures: list[str] = []
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
                soft_failures.append(pred.name)

        if soft_failures:
            return GuardResponse(
                decision=GuardDecision.REJECT,
                reason=f"governance predicate '{soft_failures[0]}' failed (soft)",
                severity=Severity.SOFT,
            )

        # 2. Authority-tier classification (BL-242, opt-in). A supplied
        # TierClassifier assigns the action a blast-radius tier; None
        # preserves L1 (tier stays None and only the static list gates).
        tier = (
            self._tier_classifier.classify(tool, arguments)
            if self._tier_classifier is not None
            else None
        )

        # 3. Approval requirement: the static approval_required list, or a
        # blast-radius tier at or above the configured threshold.
        if tool in self._contract.approval_required or (
            tier is not None and tier >= self._approval_tier
        ):
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
                tier=tier,
            )

        return GuardResponse(decision=GuardDecision.APPROVE, tier=tier)
