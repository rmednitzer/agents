"""Tests for harness.otel.OTelSink (BL-041)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from harness.events import GovernanceViolated
from harness.otel import OTelSink, _flatten
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


def test_flatten_converts_none_keeps_scalar_list_and_encodes_nested() -> None:
    attrs = _flatten(
        {
            "timestamp": "ignored",
            "none_value": None,
            "scalar_list": ["x", 1, True],
            "nested": {"a": 1},
        }
    )
    assert "timestamp" not in attrs
    assert attrs["none_value"] == ""
    assert attrs["scalar_list"] == ["x", 1, True]
    assert attrs["nested"] == '{"a": 1}'


def test_otlp_factory_wires_provider_exporter_and_processor(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Exporter:
        def __init__(self, endpoint: str | None = None) -> None:
            captured["endpoint"] = endpoint

    class _Processor:
        def __init__(self, exporter: _Exporter) -> None:
            captured["processor_exporter"] = exporter

    class _Logger:
        def emit(self, **_: Any) -> None:
            return None

    class _Provider:
        def __init__(self) -> None:
            self.processors: list[_Processor] = []

        def add_log_record_processor(self, processor: _Processor) -> None:
            self.processors.append(processor)
            captured["processor_count"] = len(self.processors)

        def get_logger(self, name: str) -> _Logger:
            captured["logger_name"] = name
            return _Logger()

    import opentelemetry.exporter.otlp.proto.http._log_exporter as exporter_mod
    import opentelemetry.sdk._logs as logs_mod
    import opentelemetry.sdk._logs.export as export_mod

    monkeypatch.setattr(exporter_mod, "OTLPLogExporter", _Exporter)
    monkeypatch.setattr(logs_mod, "LoggerProvider", _Provider)
    monkeypatch.setattr(export_mod, "BatchLogRecordProcessor", _Processor)

    sink = OTelSink.otlp("http://collector:4318/v1/logs")
    assert isinstance(sink, OTelSink)
    assert captured["endpoint"] == "http://collector:4318/v1/logs"
    assert captured["processor_count"] == 1
    assert captured["logger_name"] == "agents.harness"


def test_otlp_factory_allows_default_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Exporter:
        def __init__(self, endpoint: str | None = None) -> None:
            captured["endpoint"] = endpoint

    class _Processor:
        def __init__(self, exporter: _Exporter) -> None:
            self.exporter = exporter

    class _Logger:
        def emit(self, **_: Any) -> None:
            return None

    class _Provider:
        def add_log_record_processor(self, processor: _Processor) -> None:
            self.processor = processor

        def get_logger(self, _: str) -> _Logger:
            return _Logger()

    import opentelemetry.exporter.otlp.proto.http._log_exporter as exporter_mod
    import opentelemetry.sdk._logs as logs_mod
    import opentelemetry.sdk._logs.export as export_mod

    monkeypatch.setattr(exporter_mod, "OTLPLogExporter", _Exporter)
    monkeypatch.setattr(logs_mod, "LoggerProvider", _Provider)
    monkeypatch.setattr(export_mod, "BatchLogRecordProcessor", _Processor)

    sink = OTelSink.otlp()
    assert isinstance(sink, OTelSink)
    assert captured["endpoint"] is None
