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


def test_per_tool_quota_enforced_independently() -> None:
    """BL-073: a per-tool cap fires even when the aggregate is unset."""
    tracker = BudgetTracker(ActionBudget(max_tool_calls_per_tool={"search": 2, "delete": 1}))
    tracker.consume_tool_call(tool="search")
    tracker.consume_tool_call(tool="search")
    tracker.consume_tool_call(tool="delete")
    with pytest.raises(BudgetExceeded) as exc:
        tracker.consume_tool_call(tool="search")
    assert exc.value.budget_kind == "tool_calls:search"


def test_per_tool_quota_untracked_tool_unbounded() -> None:
    tracker = BudgetTracker(ActionBudget(max_tool_calls_per_tool={"delete": 1}))
    for _ in range(10):
        tracker.consume_tool_call(tool="search")  # not in the map => unbounded


def test_aggregate_cap_still_applies_with_per_tool() -> None:
    tracker = BudgetTracker(ActionBudget(max_tool_calls=2, max_tool_calls_per_tool={"a": 5}))
    tracker.consume_tool_call(tool="a")
    tracker.consume_tool_call(tool="a")
    with pytest.raises(BudgetExceeded) as exc:
        tracker.consume_tool_call(tool="a")
    assert exc.value.budget_kind == "tool_calls"  # aggregate fires first


def test_consume_tool_call_without_tool_is_l1_compatible() -> None:
    tracker = BudgetTracker(ActionBudget(max_tool_calls=2))
    tracker.consume_tool_call()
    tracker.consume_tool_call()
    with pytest.raises(BudgetExceeded):
        tracker.consume_tool_call()


def test_tracker_properties() -> None:
    tracker = BudgetTracker(ActionBudget(max_steps=100, max_tokens=1000))
    tracker.consume_step(3)
    tracker.consume_tokens(500)
    tracker.consume_tool_call(2)
    assert tracker.steps == 3
    assert tracker.tokens == 500
    assert tracker.tool_calls == 2
    assert tracker.budget.max_steps == 100


# --- BL-123: cost + per-tool token/wall-clock budgets ----------------


def test_consume_cost_enforces_max_cost() -> None:
    tracker = BudgetTracker(ActionBudget(max_cost_usd=1.0))
    tracker.consume_cost(0.6)
    assert tracker.cost_usd == pytest.approx(0.6)
    with pytest.raises(BudgetExceeded) as exc:
        tracker.consume_cost(0.5)
    assert exc.value.budget_kind == "cost"


def test_consume_cost_zero_is_noop() -> None:
    tracker = BudgetTracker(ActionBudget(max_cost_usd=0.0))
    tracker.consume_cost(0.0)  # no spend signal -> dimension stays 0
    assert tracker.cost_usd == 0.0


def test_per_tool_token_cap() -> None:
    tracker = BudgetTracker(ActionBudget(max_tokens_per_tool={"search": 100}))
    tracker.consume_tool_call(tool="search", tokens=60)
    with pytest.raises(BudgetExceeded) as exc:
        tracker.consume_tool_call(tool="search", tokens=50)
    assert exc.value.budget_kind == "tokens:search"


def test_per_tool_wall_clock_cap() -> None:
    tracker = BudgetTracker(ActionBudget(max_wall_clock_seconds_per_tool={"slow": 1.0}))
    tracker.consume_tool_call(tool="slow", wall_clock_seconds=0.7)
    with pytest.raises(BudgetExceeded) as exc:
        tracker.consume_tool_call(tool="slow", wall_clock_seconds=0.5)
    assert exc.value.budget_kind == "wall_clock:slow"


def test_per_tool_resource_attribution_is_opt_in() -> None:
    # No tokens/seconds passed -> exact BL-073 call-count behaviour.
    tracker = BudgetTracker(ActionBudget(max_tokens_per_tool={"x": 1}))
    tracker.consume_tool_call(tool="x")
    tracker.consume_tool_call(tool="x")  # no token cap tripped


# --- BL-154: tracker seeding + snapshot ------------------------------


def test_tracker_seeds_from_initial_counters() -> None:
    tracker = BudgetTracker(
        ActionBudget(max_tokens=100),
        initial_tokens=80,
        initial_steps=2,
        initial_tool_calls=1,
        initial_per_tool={"search": 1},
        initial_cost_usd=0.25,
    )
    assert tracker.tokens == 80
    assert tracker.steps == 2
    assert tracker.tool_calls == 1
    assert tracker.cost_usd == pytest.approx(0.25)
    with pytest.raises(BudgetExceeded):
        tracker.consume_tokens(30)  # 80 + 30 > 100


def test_snapshot_round_trips_into_seed() -> None:
    t1 = BudgetTracker(ActionBudget())
    t1.consume_tokens(10)
    t1.consume_step(2)
    t1.consume_tool_call(tool="search")
    t1.consume_cost(0.5)
    snap = t1.snapshot()
    assert snap["consumed_tokens"] == 10
    assert snap["consumed_per_tool"] == {"search": 1}
    t2 = BudgetTracker(
        ActionBudget(),
        initial_tokens=snap["consumed_tokens"],
        initial_steps=snap["consumed_steps"],
        initial_tool_calls=snap["consumed_tool_calls"],
        initial_per_tool=snap["consumed_per_tool"],
        initial_cost_usd=snap["consumed_cost_usd"],
    )
    assert t2.tokens == 10
    assert t2.cost_usd == pytest.approx(0.5)


# --- Codex review fixes: remaining("cost") and per-tool resume --------


def test_remaining_cost_returns_usd_not_seconds() -> None:
    tracker = BudgetTracker(ActionBudget(max_cost_usd=2.0))
    tracker.consume_cost(0.5)
    # Must be USD remaining (1.5), not the wall-clock fall-through.
    assert tracker.remaining("cost") == pytest.approx(1.5)


def test_remaining_cost_unbounded_is_inf() -> None:
    assert BudgetTracker(ActionBudget()).remaining("cost") == float("inf")


def test_snapshot_carries_per_tool_token_and_second_maps() -> None:
    t1 = BudgetTracker(ActionBudget())
    t1.consume_tool_call(tool="search", tokens=30, wall_clock_seconds=1.5)
    snap = t1.snapshot()
    assert snap["consumed_per_tool_tokens"] == {"search": 30}
    assert snap["consumed_per_tool_seconds"] == {"search": pytest.approx(1.5)}
    # Seeding a resumed tracker accumulates, not resets: the per-tool
    # token cap fires across the (resumed) leg boundary.
    t2 = BudgetTracker(
        ActionBudget(max_tokens_per_tool={"search": 50}),
        initial_per_tool_tokens=snap["consumed_per_tool_tokens"],
        initial_per_tool_seconds=snap["consumed_per_tool_seconds"],
    )
    with pytest.raises(BudgetExceeded) as exc:
        t2.consume_tool_call(tool="search", tokens=25)  # 30 + 25 > 50
    assert exc.value.budget_kind == "tokens:search"
