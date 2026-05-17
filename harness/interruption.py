"""Interruption pattern for human-in-the-loop approval.

When a workload proposes an action requiring approval, the enforcement
loop captures the proposal in an ApprovalInterruption, returns a
ResumableState to the caller, and halts execution. The caller approves
or denies and resumes by passing the updated state back to
run_under_contract.

The types are defined in Phase 1; the runtime-side wiring that actually
raises an interruption mid-run lands in Phase 2.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ActionRecord",
    "ApprovalInterruption",
    "Interruption",
    "ResumableState",
]


class Interruption(BaseModel):
    """Base for harness interruptions."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    created_at: datetime


class ApprovalInterruption(Interruption):
    """A proposed action requires human approval before execution."""

    kind: Literal["approval"] = "approval"
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    decision: Literal["pending", "approved", "denied"] = "pending"
    decision_reason: str | None = None


class ActionRecord(BaseModel):
    """A completed action retained for resume continuity."""

    model_config = ConfigDict(frozen=True)

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    completed_at: datetime


class ResumableState(BaseModel):
    """Immutable snapshot for resuming an interrupted run.

    Returned by run_under_contract when an approval interruption fires.
    The caller decides each pending approval via .approve / .deny, then
    passes the updated state back to run_under_contract via the `resume`
    parameter.
    """

    model_config = ConfigDict(frozen=True)

    contract_name: str
    contract_version: str
    workload: str
    input_payload: dict[str, Any]
    pending_approvals: list[ApprovalInterruption] = Field(default_factory=list)
    completed_actions: list[ActionRecord] = Field(default_factory=list)
    trace_id: str
    # Consumed budget at the pause (BL-154). The harness seeds the
    # resumed run's BudgetTracker from these so tokens, steps, tool
    # calls, and per-tool quotas accumulate across an approval pause
    # instead of restarting at zero. Defaults keep a hand-built or
    # pre-BL-154 ResumableState behaving exactly as before.
    consumed_steps: int = 0
    consumed_tokens: int = 0
    consumed_tool_calls: int = 0
    consumed_per_tool: dict[str, int] = Field(default_factory=dict)
    consumed_cost_usd: float = 0.0

    def approve(self, interruption_id: str) -> ResumableState:
        """Return a new state with the given interruption marked approved."""
        new_pending = [
            ai.model_copy(update={"decision": "approved"}) if ai.id == interruption_id else ai
            for ai in self.pending_approvals
        ]
        return self.model_copy(update={"pending_approvals": new_pending})

    def deny(self, interruption_id: str, reason: str | None = None) -> ResumableState:
        """Return a new state with the given interruption marked denied."""
        new_pending = [
            ai.model_copy(update={"decision": "denied", "decision_reason": reason})
            if ai.id == interruption_id
            else ai
            for ai in self.pending_approvals
        ]
        return self.model_copy(update={"pending_approvals": new_pending})
