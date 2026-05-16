"""Tests for harness.otel.OTelSink (BL-041)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from harness.events import GovernanceViolated
from harness.otel import OTelSink
from harness.sinks import EventSink

pytest.importorskip("opentelemetry.sdk._logs")

from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)


def _provider() -> tuple[LoggerProvider, InMemoryLogRecordExporter]:
    provider = LoggerProvider()
    exporter = InMemoryLogRecordExporter()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    return provider, exporter


def _event() -> GovernanceViolated:
    return GovernanceViolated(
        timestamp=datetime.now(UTC),
        workload="w",
        contract="c",
        contract_version="1.0",
        trace_id="trace-abc",
        span_id="span-def",
        predicate="no_rm_rf",
        severity="hard",  # type: ignore[arg-type]
        action="shell",
        action_arguments={"cmd": "rm -rf /"},
    )


def test_is_event_sink() -> None:
    provider, _ = _provider()
    assert isinstance(OTelSink(provider), EventSink)


def test_event_becomes_log_record_with_attributes() -> None:
    provider, exporter = _provider()
    sink = OTelSink(provider)
    sink.emit(_event())

    logs = exporter.get_finished_logs()
    assert len(logs) == 1
    rec = logs[0].log_record
    assert rec.body == "governance_violated"
    attrs = dict(rec.attributes or {})
    assert attrs["trace_id"] == "trace-abc"
    assert attrs["span_id"] == "span-def"
    assert attrs["predicate"] == "no_rm_rf"
    assert attrs["action"] == "shell"
    # Nested structures are JSON-encoded (OTel attrs must be scalar).
    assert attrs["action_arguments"] == '{"cmd": "rm -rf /"}'
    # timestamp is the record timestamp, not an attribute.
    assert "timestamp" not in attrs
    assert rec.timestamp is not None


def test_multiple_events_each_emit_a_record() -> None:
    provider, exporter = _provider()
    sink = OTelSink(provider)
    sink.emit(_event())
    sink.emit(_event())
    assert len(exporter.get_finished_logs()) == 2
