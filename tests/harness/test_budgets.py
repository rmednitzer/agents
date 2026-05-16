"""Tests for harness.budgets."""

from __future__ import annotations

import contextlib
import time

import pytest

from harness.budgets import ActionBudget, BudgetTracker
from harness.errors import BudgetExceeded
from harness.sinks import MemorySink


def _base() -> dict[str, str]:
    return {
        "workload": "w",
        "contract": "c",
        "contract_version": "0.1.0",
        "trace_id": "trace-1",
        "span_id": "span-1",
    }


def test_action_budget_all_optional() -> None:
    b = ActionBudget()
    assert b.max_steps is None
    assert b.max_tokens is None
    assert b.max_wall_clock_seconds is None
    assert b.max_tool_calls is None


def test_action_budget_is_frozen() -> None:
    b = ActionBudget(max_steps=10)
    try:
        b.max_steps = 20  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ActionBudget should be frozen")


def test_tracker_no_limits_never_raises() -> None:
    tracker = BudgetTracker(ActionBudget())
    for _ in range(100):
        tracker.consume_step()
        tracker.consume_tokens(1000)
        tracker.consume_tool_call()
    tracker.check_wall_clock()


def test_tracker_steps_limit_enforced() -> None:
    sink = MemorySink()
    tracker = BudgetTracker(
        ActionBudget(max_steps=3),
        sink=sink,
        base_event_fields=_base(),
    )
    tracker.consume_step()
    tracker.consume_step()
    tracker.consume_step()
    with pytest.raises(BudgetExceeded) as exc_info:
        tracker.consume_step()
    assert exc_info.value.budget_kind == "steps"
    assert exc_info.value.limit == 3.0
    assert exc_info.value.consumed == 4.0
    assert len(sink.events) == 1
    assert sink.events[0].kind == "budget_exceeded"


def test_tracker_tokens_limit_enforced() -> None:
    tracker = BudgetTracker(ActionBudget(max_tokens=100))
    tracker.consume_tokens(50)
    tracker.consume_tokens(50)
    with pytest.raises(BudgetExceeded):
        tracker.consume_tokens(1)


def test_tracker_tool_calls_limit_enforced() -> None:
    tracker = BudgetTracker(ActionBudget(max_tool_calls=2))
    tracker.consume_tool_call()
    tracker.consume_tool_call()
    with pytest.raises(BudgetExceeded):
        tracker.consume_tool_call()


def test_tracker_wall_clock_limit_enforced() -> None:
    tracker = BudgetTracker(ActionBudget(max_wall_clock_seconds=0.05))
    time.sleep(0.06)
    with pytest.raises(BudgetExceeded) as exc_info:
        tracker.check_wall_clock()
    assert exc_info.value.budget_kind == "wall_clock"


def test_tracker_remaining_returns_inf_when_unlimited() -> None:
    tracker = BudgetTracker(ActionBudget())
    assert tracker.remaining("steps") == float("inf")
    assert tracker.remaining("tokens") == float("inf")
    assert tracker.remaining("tool_calls") == float("inf")
    assert tracker.remaining("wall_clock") == float("inf")


def test_tracker_remaining_decreases() -> None:
    tracker = BudgetTracker(ActionBudget(max_steps=10))
    assert tracker.remaining("steps") == 10.0
    tracker.consume_step(3)
    assert tracker.remaining("steps") == 7.0


def test_tracker_remaining_clamps_to_zero() -> None:
    tracker = BudgetTracker(ActionBudget(max_steps=5))
    with contextlib.suppress(BudgetExceeded):
        tracker.consume_step(10)
    assert tracker.remaining("steps") == 0.0


def test_tracker_properties() -> None:
    tracker = BudgetTracker(ActionBudget(max_steps=100, max_tokens=1000))
    tracker.consume_step(3)
    tracker.consume_tokens(500)
    tracker.consume_tool_call(2)
    assert tracker.steps == 3
    assert tracker.tokens == 500
    assert tracker.tool_calls == 2
    assert tracker.budget.max_steps == 100
