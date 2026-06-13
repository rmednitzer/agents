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

from harness.authority import AuthorityTier

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
    tier: AuthorityTier | None = None
    """The proposed action's blast-radius AuthorityTier (BL-251), when a
    TierClassifier is configured (ADR 0029 surfaced this onto the guard's
    GuardResponse; ADR 0031 carries it through to the human-facing
    interruption). ``None`` when no classifier is active. Lets an
    approver (or a UI) see what kind of action they are confirming, the
    same value across the replay and deferred resume paths."""
    rollback_plan: str | None = None
    """A human-readable description of how the proposed action would be
    undone (BL-251, ADR 0031), produced by a workload-supplied
    RollbackPlanner the guard consults on the approval branch. ``None``
    when no planner is configured or the planner returns no plan. Pure
    annotation: capturing evidence around execution is a separate concern
    (BL-253, ADR 0038)."""
    restated_arguments: dict[str, Any] | None = None
    """The arguments a human re-entered when approving an irreversible
    (Tier 3) action (BL-252, ADR 0033): the two-step confirmation. The
    resume honours a Tier-IRREVERSIBLE approval only when these equal the
    proposed ``arguments`` (verified at execution, composing with the
    BL-193 (tool, arguments) binding); a missing or mismatched
    restatement re-pauses for a fresh decision. ``None`` for lower tiers,
    where a single-step approval suffices."""


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
    consumed_per_tool_tokens: dict[str, int] = Field(default_factory=dict)
    consumed_per_tool_seconds: dict[str, float] = Field(default_factory=dict)
    consumed_cost_usd: float = 0.0
    # Adapter-owned continuation state (BL-114, ADR 0027). A
    # deferred-mode `PydanticAIRuntime` stores the paused leg's
    # serialized message history here ({"mode": "deferred",
    # "messages": [...]}, JSON-able by construction) so the resumed
    # leg continues from it instead of replaying the run. `None` (the
    # default) is the replay-mode shape: a hand-built or pre-BL-114
    # state behaves exactly as before. Opaque to the harness; only
    # the runtime that produced it interprets it.
    runtime_state: dict[str, Any] | None = None

    def approve(
        self,
        interruption_id: str,
        *,
        restated_arguments: dict[str, Any] | None = None,
    ) -> ResumableState:
        """Return a new state with the given interruption marked approved.

        For an irreversible (Tier 3) action, pass ``restated_arguments``:
        the resume honours the approval only if they match the proposed
        arguments (BL-252, ADR 0033, the two-step confirmation). A missing
        or mismatched restatement re-pauses for a fresh decision. Ignored
        for lower tiers, where a single-step approval suffices.
        """
        new_pending = [
            ai.model_copy(update={"decision": "approved", "restated_arguments": restated_arguments})
            if ai.id == interruption_id
            else ai
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
