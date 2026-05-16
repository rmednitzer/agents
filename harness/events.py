"""Structured events emitted by the harness.

Events are immutable Pydantic models with OTel-compatible identifiers
(trace_id, span_id, parent_span_id) so an OTel sink can be added in a
future phase without changing event producers.

The base HarnessEvent is intentionally not used directly; concrete
subtypes carry a Literal `kind` discriminator for downstream parsers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.contract import Severity

__all__ = [
    "ApprovalDenied",
    "ApprovalGranted",
    "ApprovalRequested",
    "BudgetExceededEvent",
    "ContractCompleted",
    "ContractStarted",
    "GovernanceViolated",
    "HarnessEvent",
    "InvariantViolated",
    "PostconditionViolated",
    "PreconditionViolated",
    "SkillDispatched",
]


class HarnessEvent(BaseModel):
    """Base for all harness events. Immutable, OTel-ready."""

    model_config = ConfigDict(frozen=True)

    kind: str
    timestamp: datetime
    workload: str
    contract: str
    contract_version: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None


class ContractStarted(HarnessEvent):
    kind: Literal["contract_started"] = "contract_started"


class ContractCompleted(HarnessEvent):
    kind: Literal["contract_completed"] = "contract_completed"
    duration_ms: float


class PreconditionViolated(HarnessEvent):
    kind: Literal["precondition_violated"] = "precondition_violated"
    predicate: str
    severity: Severity
    state_snapshot: dict[str, Any] = Field(default_factory=dict)


class PostconditionViolated(HarnessEvent):
    kind: Literal["postcondition_violated"] = "postcondition_violated"
    predicate: str
    severity: Severity
    state_snapshot: dict[str, Any] = Field(default_factory=dict)


class InvariantViolated(HarnessEvent):
    kind: Literal["invariant_violated"] = "invariant_violated"
    predicate: str
    severity: Severity
    state_snapshot: dict[str, Any] = Field(default_factory=dict)


class GovernanceViolated(HarnessEvent):
    kind: Literal["governance_violated"] = "governance_violated"
    predicate: str
    severity: Severity
    action: str
    action_arguments: dict[str, Any] = Field(default_factory=dict)


class BudgetExceededEvent(HarnessEvent):
    kind: Literal["budget_exceeded"] = "budget_exceeded"
    budget_kind: str
    limit: float
    consumed: float


class ApprovalRequested(HarnessEvent):
    kind: Literal["approval_requested"] = "approval_requested"
    interruption_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ApprovalGranted(HarnessEvent):
    kind: Literal["approval_granted"] = "approval_granted"
    interruption_id: str
    tool: str


class ApprovalDenied(HarnessEvent):
    kind: Literal["approval_denied"] = "approval_denied"
    interruption_id: str
    tool: str
    reason: str | None = None


class SkillDispatched(HarnessEvent):
    kind: Literal["skill_dispatched"] = "skill_dispatched"
    skill: str
    dispatcher: str
    confidence: float
    rationale: str
