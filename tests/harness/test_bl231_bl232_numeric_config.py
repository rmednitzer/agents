"""Twelfth-audit numeric-configuration validation: `BL-231` / `BL-232`
(ADR 0022).

The non-finite-numeric class (`NaN` / `+inf` slips through a comparison
because every comparison with `NaN` is False, and `+inf <= 0` is also
False) was closed at the *value / data* boundaries by BL-159
(cosine), BL-205 (MultiDispatcher weights), BL-221 (the floats a caller
*consumes* into `BudgetTracker`), and BL-226 (S3 metadata), and at the
`Namespace.retention_seconds` *config* boundary by BL-197. This audit
generalises it to the remaining numeric-*configuration* boundaries that
were never brought to the BL-197 standard:

`BL-231` (missing guard): `ActionBudget` and `RetryPolicy` had no
finiteness / sign validation on their numeric fields. A `NaN` / `+inf`
budget limit makes the tracker's `consumed > limit` check always False,
silently disabling the ceiling (the dual of BL-221, which hardened the
`consumed` side of the same comparison). A `NaN` backoff makes
`RetryPolicy.delay_for` non-finite and `asyncio.sleep(NaN)` returns
immediately, turning the bounded backoff into a no-delay retry storm.

`BL-232` (guard with a `NaN` hole): `MCPServerSpec.timeout_seconds`
rejected non-positive values with `<= 0`, but `NaN <= 0` and
`+inf <= 0` are both False, so a non-finite timeout passed a guard that
explicitly claims "must be positive". (`TTLSweeper.interval_seconds`,
the twin, is covered in `tests/memory/test_bl232_sweeper_interval.py`.)
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from harness.budgets import ActionBudget, BudgetTracker
from harness.errors import BudgetExceeded
from harness.mcp import MCPServerSpec, MCPTransport
from harness.runtime import RetryPolicy

_BASE_EVENT_FIELDS = {
    "workload": "wl",
    "contract": "c",
    "contract_version": "0.0.1",
    "trace_id": "t",
    "span_id": "s",
}


# --- BL-231: ActionBudget -------------------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1.0])
def test_action_budget_rejects_bad_float_cost(bad: float) -> None:
    with pytest.raises(ValidationError):
        ActionBudget(max_cost_usd=bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -0.5])
def test_action_budget_rejects_bad_float_wall_clock(bad: float) -> None:
    with pytest.raises(ValidationError):
        ActionBudget(max_wall_clock_seconds=bad)


@pytest.mark.parametrize("field", ["max_steps", "max_tokens", "max_tool_calls"])
def test_action_budget_rejects_negative_int_limits(field: str) -> None:
    with pytest.raises(ValidationError):
        ActionBudget(**{field: -1})


def test_action_budget_rejects_nan_in_per_tool_wall_clock_map() -> None:
    with pytest.raises(ValidationError):
        ActionBudget(max_wall_clock_seconds_per_tool={"slow": float("nan")})


def test_action_budget_rejects_negative_in_per_tool_int_maps() -> None:
    with pytest.raises(ValidationError):
        ActionBudget(max_tool_calls_per_tool={"search": -2})
    with pytest.raises(ValidationError):
        ActionBudget(max_tokens_per_tool={"search": -2})


def test_action_budget_accepts_none_zero_and_finite_positive() -> None:
    """Every legitimate spec is unaffected: None (unlimited), 0 (zero
    ceiling, exercised by the existing cost test), and finite positive."""
    ActionBudget()  # all None
    ActionBudget(max_cost_usd=0.0)  # zero ceiling is valid
    ActionBudget(
        max_steps=10,
        max_tokens=1000,
        max_wall_clock_seconds=5.0,
        max_tool_calls=3,
        max_cost_usd=1.5,
        max_tool_calls_per_tool={"search": 3, "delete": 1},
        max_tokens_per_tool={"search": 100},
        max_wall_clock_seconds_per_tool={"slow": 2.0},
    )


def test_nan_cost_limit_would_have_disabled_the_ceiling() -> None:
    """Pin the bug BL-231 prevents: before the validator, a NaN cost
    limit made ``consumed > NaN`` always False, so the tracker never
    raised no matter how much was consumed. The construction now fails
    closed instead of shipping a silently-disabled ceiling."""
    with pytest.raises(ValidationError):
        ActionBudget(max_cost_usd=float("nan"))
    # A *finite* ceiling still fires, demonstrating the dimension works.
    tracker = BudgetTracker(ActionBudget(max_cost_usd=1.0), base_event_fields=_BASE_EVENT_FIELDS)
    with pytest.raises(BudgetExceeded):
        tracker.consume_cost(2.0)


# --- BL-231: RetryPolicy --------------------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -0.1])
def test_retry_policy_rejects_bad_backoff_base(bad: float) -> None:
    with pytest.raises(ValueError, match="backoff_base_seconds"):
        RetryPolicy(backoff_base_seconds=bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1.0])
def test_retry_policy_rejects_bad_backoff_max(bad: float) -> None:
    with pytest.raises(ValueError, match="backoff_max_seconds"):
        RetryPolicy(backoff_max_seconds=bad)


def test_retry_policy_rejects_negative_max_retries() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        RetryPolicy(max_retries=-1)


@pytest.mark.parametrize("bad", [0, -1])
def test_retry_policy_rejects_non_positive_circuit_breaker(bad: int) -> None:
    with pytest.raises(ValueError, match="circuit_breaker_threshold"):
        RetryPolicy(circuit_breaker_threshold=bad)


def test_retry_policy_accepts_defaults_and_finite_values() -> None:
    RetryPolicy()  # the documented one-attempt default
    policy = RetryPolicy(
        max_retries=3,
        backoff_base_seconds=0.5,
        backoff_max_seconds=30.0,
        circuit_breaker_threshold=5,
    )
    # The backoff stays finite, so asyncio.sleep gets a real delay (not
    # the NaN that would short-circuit to a no-delay retry storm).
    assert math.isfinite(policy.delay_for(1))
    assert policy.delay_for(10) == 30.0  # capped by backoff_max_seconds


def test_nan_backoff_would_have_defeated_the_delay() -> None:
    """Pin the bug BL-231 prevents: a NaN backoff made ``delay_for``
    NaN, and ``asyncio.sleep(NaN)`` returns immediately, so retries
    hammered the failing provider with no delay. Construction now
    rejects it; a finite policy yields a usable, monotonic delay."""
    with pytest.raises(ValueError, match="backoff_base_seconds"):
        RetryPolicy(backoff_base_seconds=float("nan"))
    policy = RetryPolicy(backoff_base_seconds=0.5, backoff_max_seconds=30.0)
    assert policy.delay_for(1) == 0.5
    assert policy.delay_for(2) == 1.0


# --- BL-232: MCPServerSpec.timeout_seconds --------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_mcp_spec_rejects_non_finite_timeout(bad: float) -> None:
    """``NaN`` / ``+inf`` slipped through the old ``<= 0`` guard because
    both comparisons are False; the ``math.isfinite`` conjunct closes it."""
    with pytest.raises(ValidationError):
        MCPServerSpec(
            name="x",
            transport=MCPTransport.STDIO,
            command="c",
            timeout_seconds=bad,
        )


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_mcp_spec_still_rejects_non_positive_timeout(bad: float) -> None:
    with pytest.raises(ValidationError):
        MCPServerSpec(
            name="x",
            transport=MCPTransport.STDIO,
            command="c",
            timeout_seconds=bad,
        )


def test_mcp_spec_accepts_positive_finite_timeout() -> None:
    spec = MCPServerSpec(name="x", transport=MCPTransport.STDIO, command="c", timeout_seconds=5.0)
    assert spec.timeout_seconds == 5.0
    # Default is untouched.
    default = MCPServerSpec(name="x", transport=MCPTransport.STDIO, command="c")
    assert default.timeout_seconds == 30.0
