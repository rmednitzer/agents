"""Sixth-audit harness fixes: regression tests for `BL-200` / `BL-201`
/ `BL-202` / `BL-203` (ADR 0015).

`BL-200` (Redactor recursion cap): a cyclic or pathologically deep
event payload crashed the redactor with `RecursionError`; the
audit-path-must-not-crash invariant (BL-167) is now enforced by a
configurable depth cap that replaces over-deep containers with the
placeholder.

`BL-201` (OpenAI batch non-dict line): `_decode_lines` used to
`json.loads` every non-blank line and yield it directly; a row whose
JSON decoded to `null` / a number / a string crashed downstream
iteration. The decoder now wraps malformed rows in a placeholder dict
that lands in the consumer's existing errored-row branch (BL-189
class extension).

`BL-202` (Wall-clock boundary event parity): the runtime raised
`BudgetExceeded("wall_clock", ...)` directly when its end-to-end
deadline tripped at the exact boundary instant (where the tracker's
strict `>` did not fire), with no `BudgetExceededEvent` in the audit
stream. The runtime now emits the event via the new
`BudgetTracker.emit_wall_clock_exceeded` method before the bare raise
(BL-189 / BL-167 audit-vs-raise parity class).

`BL-203` (Resume-validation orphan emit): `run_under_contract` emitted
`ContractStarted` before validating the resume state's pending
approvals, so a malformed `ResumableState` left an orphan
`ContractStarted` with no matching terminal event and no `RunRecord`.
The validation now runs FIRST, before any emit, so every emitted
`ContractStarted` is matched by a terminal event (BL-167 class
extension).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from harness.budgets import ActionBudget, BudgetTracker
from harness.events import (
    BudgetExceededEvent,
    ContractStarted,
    HarnessEvent,
    PostconditionViolated,
)
from harness.interruption import ApprovalInterruption, ResumableState
from harness.openai_api import _decode_lines
from harness.redaction import RedactingSink, Redactor
from harness.sinks import EventSink


class _CaptureSink:
    """Trivial EventSink that records every emitted event."""

    def __init__(self) -> None:
        self.events: list[HarnessEvent] = []

    def emit(self, event: HarnessEvent) -> None:
        self.events.append(event)


def _base() -> dict[str, Any]:
    return {
        "workload": "w",
        "contract": "c",
        "contract_version": "v1",
        "trace_id": "t" * 32,
        "span_id": "s" * 16,
    }


# --- BL-200: Redactor recursion cap -----------------------------------


def test_redactor_handles_cyclic_dict() -> None:
    """A self-referential dict in an event payload must not crash the
    redactor. Pre-`BL-200` `_scrub` recursed infinitely until Python's
    stack overflow killed the emit and aborted the audit path."""
    payload: dict[str, Any] = {"k": "v"}
    payload["self"] = payload
    event = PostconditionViolated(
        timestamp=datetime.now(UTC),
        predicate="p",
        severity="hard",
        stage="postcondition",
        state_snapshot=payload,
        **_base(),
    )
    redacted = Redactor(max_depth=8).redact(event)
    # The redact did not raise. The over-deep self-reference was
    # replaced by the placeholder somewhere in the structure.
    snap = redacted.state_snapshot
    assert isinstance(snap, dict)
    assert "[REDACTED]" in repr(snap)


def test_redactor_caps_pathologically_deep_payload() -> None:
    """A 200-level-deep dict (well past `max_depth=64`) is capped
    rather than crashing the emit chain."""
    deep: Any = "leaf"
    for _ in range(200):
        deep = {"x": deep}
    event = PostconditionViolated(
        timestamp=datetime.now(UTC),
        predicate="p",
        severity="hard",
        stage="postcondition",
        state_snapshot=deep,
        **_base(),
    )
    redacted = Redactor().redact(event)
    # The top is still a dict; the deep tail was clamped to the
    # placeholder somewhere along the chain. Walk just past the cap to
    # confirm we hit the clamp.
    cur: Any = redacted.state_snapshot
    for _ in range(80):
        if isinstance(cur, dict) and "x" in cur:
            cur = cur["x"]
        else:
            break
    assert cur == "[REDACTED]"


def test_redactor_preserves_shallow_payloads() -> None:
    """A normal-depth payload is unchanged by the cap (the cap only
    bites pathological inputs)."""
    payload = {"a": {"b": {"c": "leaf"}}}
    event = PostconditionViolated(
        timestamp=datetime.now(UTC),
        predicate="p",
        severity="hard",
        stage="postcondition",
        state_snapshot=payload,
        **_base(),
    )
    redacted = Redactor().redact(event)
    assert redacted.state_snapshot == payload


def test_redacting_sink_does_not_crash_on_cyclic_payload() -> None:
    """End-to-end: the `RedactingSink` boundary survives a cyclic
    payload (would previously kill the emit chain via
    `RecursionError`)."""
    payload: dict[str, Any] = {}
    payload["self"] = payload
    inner = _CaptureSink()
    sink = RedactingSink(inner=inner, redactor=Redactor(max_depth=8))
    event = PostconditionViolated(
        timestamp=datetime.now(UTC),
        predicate="p",
        severity="hard",
        stage="postcondition",
        state_snapshot=payload,
        **_base(),
    )
    sink.emit(event)  # must not raise
    assert len(inner.events) == 1


# --- BL-201: OpenAI batch non-dict line -------------------------------


class _Payload:
    """Stand-in for the OpenAI SDK's binary-response wrapper."""

    def __init__(self, text: str) -> None:
        self.text = text


def test_decode_lines_yields_placeholder_for_bare_null() -> None:
    """A JSONL row that decodes to bare `null` is yielded as a
    placeholder dict, not raised as `AttributeError`. Pre-`BL-201`
    the next consumer call `line.get(...)` crashed and the iteration
    stopped mid-file."""
    out = list(_decode_lines(_Payload('{"a":1}\nnull\n{"b":2}\n')))
    assert len(out) == 3
    assert out[0] == {"a": 1}
    assert out[1] == {"_malformed": True, "_raw": "None"}
    assert out[2] == {"b": 2}


def test_decode_lines_yields_placeholder_for_bare_number() -> None:
    out = list(_decode_lines(_Payload('{"a":1}\n42\n{"b":2}\n')))
    assert len(out) == 3
    assert out[1] == {"_malformed": True, "_raw": "42"}


def test_decode_lines_yields_placeholder_for_bare_array() -> None:
    out = list(_decode_lines(_Payload('{"a":1}\n[1,2,3]\n')))
    assert len(out) == 2
    assert out[1] == {"_malformed": True, "_raw": "[1, 2, 3]"}


def test_decode_lines_yields_placeholder_for_undecodable_line() -> None:
    """A truly unparseable line is also yielded as a placeholder so
    the iteration continues."""
    out = list(_decode_lines(_Payload('{"a":1}\n}not-json{\n{"b":2}\n')))
    assert len(out) == 3
    # Placeholder carries the raw text (clamped to 120 chars).
    assert out[1]["_malformed"] is True


def test_decode_lines_unchanged_for_well_formed_input() -> None:
    """Backward compatibility: a well-formed JSONL stream yields the
    same dicts as before."""
    out = list(_decode_lines(_Payload('{"a":1}\n{"b":2}\n')))
    assert out == [{"a": 1}, {"b": 2}]


# --- BL-202: Wall-clock boundary event parity -------------------------


def test_emit_wall_clock_exceeded_emits_event_with_base_fields() -> None:
    """The new `BudgetTracker.emit_wall_clock_exceeded` method
    surfaces a `BudgetExceededEvent` to the configured sink, so the
    runtime's boundary-fallback raise pairs with the audit stream."""
    sink = _CaptureSink()
    tracker = BudgetTracker(
        ActionBudget(max_wall_clock_seconds=10.0),
        sink=sink,
        base_event_fields=_base(),
    )
    tracker.emit_wall_clock_exceeded(elapsed=10.0)
    assert len(sink.events) == 1
    evt = sink.events[0]
    assert isinstance(evt, BudgetExceededEvent)
    assert evt.budget_kind == "wall_clock"
    assert evt.limit == 10.0
    assert evt.consumed == 10.0


def test_emit_wall_clock_exceeded_silent_without_base() -> None:
    """No base fields, no emission (BL-040 silent-by-default
    convention)."""
    sink = _CaptureSink()
    tracker = BudgetTracker(
        ActionBudget(max_wall_clock_seconds=10.0),
        sink=sink,
    )
    tracker.emit_wall_clock_exceeded(elapsed=10.0)
    assert sink.events == []


def test_emit_wall_clock_exceeded_silent_without_limit() -> None:
    """No wall_clock limit, no emission (the event would have a
    nonsensical limit)."""
    sink = _CaptureSink()
    tracker = BudgetTracker(
        ActionBudget(),  # no max_wall_clock_seconds
        sink=sink,
        base_event_fields=_base(),
    )
    tracker.emit_wall_clock_exceeded(elapsed=10.0)
    assert sink.events == []


# --- BL-203: Resume-validation orphan emit ----------------------------


class _NoopRuntime:
    """Minimal Runtime double for the resume-validation tests."""

    name: str = "noop"

    def __init__(self, output: Any) -> None:
        self._output = output

    async def run(self, prompt: str, **_: Any) -> Any:
        return self._output

    def stream(self, prompt: str, **_: Any) -> Any:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_resume_with_unresolved_approval_does_not_emit_contract_started() -> None:
    """A `ResumableState` with pending approvals raises `ValueError`
    BEFORE any `ContractStarted` is emitted, so the audit stream
    does not carry an orphan event with no terminal partner."""
    from pydantic import BaseModel

    from harness.contract import Contract

    class _In(BaseModel):
        x: int

    class _Out(BaseModel):
        y: int

    from harness.enforcement import run_under_contract

    contract = Contract[_In, _Out](name="audit6.bl203", version="1")

    sink = _CaptureSink()
    state = ResumableState(
        contract_name="audit6.bl203",
        contract_version="1",
        workload="w",
        input_payload={"x": 1},
        trace_id="t" * 32,
        pending_approvals=[
            ApprovalInterruption(
                id="r1",
                created_at=datetime.now(UTC),
                tool="t",
                arguments={},
                decision="pending",
            )
        ],
    )
    with pytest.raises(ValueError, match="approvals still pending"):
        await run_under_contract(
            runtime=_NoopRuntime(_Out(y=2)),
            contract=contract,
            input=_In(x=1),
            output_model=_Out,
            sink=sink,
            resume=state,
        )

    # No emit at all (the validation raised before active_sink was
    # used). Critically, no orphan ContractStarted.
    kinds = [e.kind for e in sink.events]
    assert "contract_started" not in kinds


@pytest.mark.asyncio
async def test_normal_resume_still_emits_contract_started() -> None:
    """Backward compatibility: a well-formed resume (no pending
    approvals) still emits `ContractStarted` (the validation is a
    no-op when no approval is pending)."""
    from pydantic import BaseModel

    from harness.contract import Contract

    class _In(BaseModel):
        x: int

    class _Out(BaseModel):
        y: int

    from harness.enforcement import run_under_contract

    contract = Contract[_In, _Out](name="audit6.bl203b", version="1")

    sink = _CaptureSink()
    state = ResumableState(
        contract_name="audit6.bl203b",
        contract_version="1",
        workload="w",
        input_payload={"x": 1},
        trace_id="u" * 32,
        pending_approvals=[],
    )
    out = await run_under_contract(
        runtime=_NoopRuntime(_Out(y=2)),
        contract=contract,
        input=_In(x=1),
        output_model=_Out,
        sink=sink,
        resume=state,
    )
    assert out.y == 2
    kinds = [e.kind for e in sink.events]
    assert "contract_started" in kinds


# Quiet ruff F401 by referencing the otherwise-imported types.
_ = (EventSink, ContractStarted, json)
