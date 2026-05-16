"""Action budgets for the harness.

ActionBudget is an immutable spec. BudgetTracker is the per-run mutable
counter (the only mutable harness object). The runtime adapter is
responsible for calling consume_step / consume_tokens / consume_tool_call
/ check_wall_clock at the right points; the tracker raises BudgetExceeded
(and emits BudgetExceededEvent) on overflow.

Wall-clock enforcement is reactive: callers must invoke check_wall_clock
at known checkpoints (e.g. before each step). Background timing is out of
scope for L1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from harness.errors import BudgetExceeded
from harness.events import BudgetExceededEvent
from harness.sinks import EventSink, NullSink

__all__ = [
    "ActionBudget",
    "BudgetKind",
    "BudgetTracker",
]

BudgetKind = Literal["steps", "tokens", "wall_clock", "tool_calls"]


class ActionBudget(BaseModel):
    """Immutable action budget spec.

    All fields are optional; None means unlimited for that dimension.
    """

    model_config = ConfigDict(frozen=True)

    max_steps: int | None = None
    max_tokens: int | None = None
    max_wall_clock_seconds: float | None = None
    max_tool_calls: int | None = None


class BudgetTracker:
    """Mutable per-run budget counter.

    Constructed by the harness at the start of a run. Passed to the
    runtime adapter, which calls consume_* methods at the appropriate
    points. The first consume call that exceeds a limit emits a
    BudgetExceededEvent and raises BudgetExceeded.

    The tracker holds the base event fields (workload, contract,
    trace_id, span_id, contract_version) so it can emit cleanly without
    requiring the caller to assemble them.
    """

    def __init__(
        self,
        budget: ActionBudget,
        *,
        sink: EventSink | None = None,
        base_event_fields: dict[str, Any] | None = None,
    ) -> None:
        self._budget = budget
        self._sink: EventSink = sink if sink is not None else NullSink()
        self._base = base_event_fields if base_event_fields is not None else {}
        self._steps = 0
        self._tokens = 0
        self._tool_calls = 0
        self._started_at = datetime.now(UTC)

    @property
    def budget(self) -> ActionBudget:
        return self._budget

    @property
    def steps(self) -> int:
        return self._steps

    @property
    def tokens(self) -> int:
        return self._tokens

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    def consume_step(self, n: int = 1) -> None:
        """Record n steps consumed and enforce the steps limit."""
        self._steps += n
        self._check("steps", float(self._steps), self._budget.max_steps)

    def consume_tokens(self, n: int) -> None:
        """Record n tokens consumed and enforce the tokens limit."""
        self._tokens += n
        self._check("tokens", float(self._tokens), self._budget.max_tokens)

    def consume_tool_call(self, n: int = 1) -> None:
        """Record n tool calls consumed and enforce the tool_calls limit."""
        self._tool_calls += n
        self._check("tool_calls", float(self._tool_calls), self._budget.max_tool_calls)

    def check_wall_clock(self) -> None:
        """Check elapsed wall-clock time against max_wall_clock_seconds."""
        elapsed = (datetime.now(UTC) - self._started_at).total_seconds()
        self._check("wall_clock", elapsed, self._budget.max_wall_clock_seconds)

    def remaining(self, kind: BudgetKind) -> float:
        """Return remaining budget for the given kind.

        Returns float('inf') if no limit is set for that kind.
        """
        if kind == "steps":
            return _remaining(self._steps, self._budget.max_steps)
        if kind == "tokens":
            return _remaining(self._tokens, self._budget.max_tokens)
        if kind == "tool_calls":
            return _remaining(self._tool_calls, self._budget.max_tool_calls)
        elapsed = (datetime.now(UTC) - self._started_at).total_seconds()
        return _remaining(elapsed, self._budget.max_wall_clock_seconds)

    def _check(self, kind: str, consumed: float, limit: float | int | None) -> None:
        if limit is None:
            return
        if consumed > float(limit):
            if self._base:
                self._sink.emit(
                    BudgetExceededEvent(
                        timestamp=datetime.now(UTC),
                        budget_kind=kind,
                        limit=float(limit),
                        consumed=consumed,
                        **self._base,
                    )
                )
            raise BudgetExceeded(kind, float(limit), consumed)


def _remaining(consumed: float, limit: float | int | None) -> float:
    if limit is None:
        return float("inf")
    return max(0.0, float(limit) - consumed)
