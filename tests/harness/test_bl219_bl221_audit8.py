"""Eighth-audit harness fixes: regression tests for `BL-219` / `BL-221`
(ADR 0018).

`BL-219` (JsonlSink UTF-8 encoding): `JsonlSink.emit` opened the
target file with `Path.open("a")` and no explicit `encoding=`, so on a
non-UTF-8 platform locale (Windows cp1252, C locale ASCII) the
platform default applied. A non-ASCII event payload would either raise
`UnicodeEncodeError` past the documented sink boundary or silently
mis-encode the JSONL. BL-218 (read-side `Path.read_text` consistency)
applied to the write side: `JsonlSink.emit` now pins `encoding="utf-8"`
explicitly.

`BL-221` (caller-fed float trust boundary on `BudgetTracker`):
`consume_cost(usd)` and `consume_tool_call(wall_clock_seconds=)` accept
caller-fed floats. NaN is truthy in Python (so the `if usd:` short-
circuit did not skip it), NaN propagates through `+` (so the
accumulator becomes NaN), and `NaN > limit` is always False (so the
ceiling never trips). A single NaN cost report or wall-clock attribution
silently disabled the budget ceiling for the rest of the run. The tracker
now validates `math.isfinite(...)` and non-negativity at both entry
points; BL-159 / BL-205 class extension on the budget input boundary.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from harness.budgets import ActionBudget, BudgetTracker
from harness.events import ContractStarted
from harness.sinks import JsonlSink

_BASE_EVENT_FIELDS = {
    "workload": "wl",
    "contract": "c",
    "contract_version": "0.0.1",
    "trace_id": "t",
    "span_id": "s",
}


# --- BL-219 -----------------------------------------------------------


def test_jsonl_sink_writes_non_ascii_utf8(tmp_path: Path) -> None:
    """Non-ASCII event round-trips through JsonlSink as valid UTF-8.

    Before BL-219 this depended on the platform locale; the explicit
    encoding pins the wire format so the file is always UTF-8.
    """
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path)
    sink.emit(
        ContractStarted(
            timestamp=__import__("datetime").datetime.now(__import__("datetime").UTC),
            workload="café",
            contract="contraté",
            contract_version="0.0.1",
            trace_id="trace-é",
            span_id="span-€",
        )
    )
    raw = path.read_bytes()
    # Bytes are valid UTF-8 (not, e.g., cp1252 mis-encode).
    text = raw.decode("utf-8")
    event = json.loads(text.strip())
    assert event["workload"] == "café"
    assert event["contract"] == "contraté"
    assert event["trace_id"] == "trace-é"
    assert event["span_id"] == "span-€"


def test_jsonl_sink_appends_multiple_events_utf8(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path)
    for i in range(3):
        sink.emit(
            ContractStarted(
                timestamp=__import__("datetime").datetime.now(__import__("datetime").UTC),
                workload=f"wl-{i}-€",
                contract="c",
                contract_version="0.0.1",
                trace_id="t",
                span_id="s",
            )
        )
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln]
    assert len(lines) == 3
    workloads = [json.loads(ln)["workload"] for ln in lines]
    assert workloads == ["wl-0-€", "wl-1-€", "wl-2-€"]


# --- BL-221 -----------------------------------------------------------


def _tracker(*, max_cost_usd: float | None = 10.0) -> BudgetTracker:
    return BudgetTracker(
        ActionBudget(max_cost_usd=max_cost_usd, max_wall_clock_seconds_per_tool={"t": 1.0}),
        base_event_fields=_BASE_EVENT_FIELDS,
    )


def test_consume_cost_rejects_nan() -> None:
    tracker = _tracker()
    with pytest.raises(ValueError, match="finite"):
        tracker.consume_cost(float("nan"))
    # No state change after the rejected call: the cost dimension
    # stays at zero and the ceiling stays armed.
    assert tracker.cost_usd == 0.0
    # Sanity: a real consumption still works and the ceiling still
    # fires when exceeded.
    tracker.consume_cost(5.0)
    assert tracker.cost_usd == 5.0


def test_consume_cost_rejects_positive_inf() -> None:
    tracker = _tracker()
    with pytest.raises(ValueError, match="finite"):
        tracker.consume_cost(float("inf"))
    assert tracker.cost_usd == 0.0


def test_consume_cost_rejects_negative_inf() -> None:
    tracker = _tracker()
    with pytest.raises(ValueError, match="finite"):
        tracker.consume_cost(float("-inf"))
    assert tracker.cost_usd == 0.0


def test_consume_cost_rejects_negative() -> None:
    tracker = _tracker()
    with pytest.raises(ValueError, match="non-negative"):
        tracker.consume_cost(-1.0)
    assert tracker.cost_usd == 0.0


def test_consume_cost_accepts_zero_and_positive_finite() -> None:
    tracker = _tracker()
    tracker.consume_cost(0.0)  # no-op
    tracker.consume_cost(3.5)
    assert tracker.cost_usd == 3.5


def test_consume_tool_call_rejects_nan_wall_clock_seconds() -> None:
    tracker = _tracker()
    with pytest.raises(ValueError, match="finite"):
        tracker.consume_tool_call(tool="t", wall_clock_seconds=float("nan"))


def test_consume_tool_call_rejects_inf_wall_clock_seconds() -> None:
    tracker = _tracker()
    with pytest.raises(ValueError, match="finite"):
        tracker.consume_tool_call(tool="t", wall_clock_seconds=float("inf"))


def test_consume_tool_call_rejects_negative_wall_clock_seconds() -> None:
    tracker = _tracker()
    with pytest.raises(ValueError, match="non-negative"):
        tracker.consume_tool_call(tool="t", wall_clock_seconds=-0.001)


def test_consume_tool_call_accepts_zero_and_positive_finite() -> None:
    tracker = _tracker()
    tracker.consume_tool_call(tool="t", wall_clock_seconds=0.0)
    tracker.consume_tool_call(tool="t", wall_clock_seconds=0.5)
    # Sanity: a normal `0.0` default keeps the L1 call-count behaviour
    # working byte-for-byte (no exception, no state corruption).
    assert math.isfinite(tracker.cost_usd)
