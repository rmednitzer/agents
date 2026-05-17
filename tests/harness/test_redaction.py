"""Tests for harness.redaction (BL-130)."""

from __future__ import annotations

from datetime import UTC, datetime

from harness.contract import Severity
from harness.events import ApprovalRequested, ContractStarted, GovernanceViolated
from harness.redaction import RedactingSink, Redactor
from harness.sinks import EventSink, MemorySink

_BASE = {
    "timestamp": datetime.now(UTC),
    "workload": "w",
    "contract": "c",
    "contract_version": "0.1.0",
    "trace_id": "t",
    "span_id": "s",
}


def _governance(args: dict[str, object]) -> GovernanceViolated:
    return GovernanceViolated(
        **_BASE,
        predicate="p",
        severity=Severity.SOFT,
        action="call_tool",
        action_arguments=args,
    )


def test_redacts_sensitive_argument_names() -> None:
    e = _governance({"api_key": "shh", "query": "hello", "Authorization": "Bearer abc"})
    out = Redactor().redact(e)
    assert out.action_arguments["api_key"] == "[REDACTED]"
    assert out.action_arguments["Authorization"] == "[REDACTED]"
    assert out.action_arguments["query"] == "hello"


def test_redacts_secret_shaped_values() -> None:
    e = _governance({"note": "key is AKIA1234567890ABCDEF here", "n": 1})
    out = Redactor().redact(e)
    assert out.action_arguments["note"] == "[REDACTED]"
    assert out.action_arguments["n"] == 1


def test_clamps_oversized_scalar() -> None:
    e = _governance({"blob": "x" * 50})
    out = Redactor(max_value_chars=10).redact(e)
    assert out.action_arguments["blob"] == "x" * 10 + "...[clamped]"


def test_recurses_into_nested_structures() -> None:
    e = _governance({"outer": {"password": "p", "ok": "v"}, "list": [{"token": "t"}, "plain"]})
    out = Redactor().redact(e)
    assert out.action_arguments["outer"] == {"password": "[REDACTED]", "ok": "v"}
    assert out.action_arguments["list"] == [{"token": "[REDACTED]"}, "plain"]


def test_event_without_dict_fields_returned_unchanged() -> None:
    e = ContractStarted(**_BASE)
    out = Redactor().redact(e)
    assert out is e


def test_original_event_is_not_mutated() -> None:
    e = _governance({"secret": "leak"})
    out = Redactor().redact(e)
    assert e.action_arguments == {"secret": "leak"}
    assert out is not e
    assert out.action_arguments == {"secret": "[REDACTED]"}


def test_approval_requested_arguments_are_redacted() -> None:
    e = ApprovalRequested(
        **_BASE,
        interruption_id="i1",
        tool="delete",
        arguments={"client_secret": "xyz", "path": "/tmp/x"},
    )
    out = Redactor().redact(e)
    assert out.arguments["client_secret"] == "[REDACTED]"
    assert out.arguments["path"] == "/tmp/x"


def test_redacting_sink_forwards_redacted_copy() -> None:
    inner = MemorySink()
    sink: EventSink = RedactingSink(inner)
    assert isinstance(sink, EventSink)
    sink.emit(_governance({"password": "p"}))
    assert len(inner.events) == 1
    emitted = inner.events[0]
    assert isinstance(emitted, GovernanceViolated)
    assert emitted.action_arguments == {"password": "[REDACTED]"}


def test_redacting_sink_passes_through_clean_events() -> None:
    inner = MemorySink()
    RedactingSink(inner).emit(ContractStarted(**_BASE))
    assert inner.events[0].kind == "contract_started"
