"""OTel-Collector-compatible EventSink (BL-041, ADR 0007).

ADR 0002 designed every HarnessEvent to carry OTel-compatible
identifiers (trace_id / span_id / parent_span_id) so an OTel sink could
be added later "without touching event producers". This is that sink.

``opentelemetry-sdk`` is an optional dependency, imported lazily
(``pip install 'agents[otel]'``). Each event becomes one OTel log
record: body = the event ``kind``, the event's scalar fields become log
attributes, and trace_id / span_id / parent_span_id ride along as
attributes so an OTel Collector can correlate them with spans. (The
logs SDK's trace-context plumbing is still unstable upstream;
attribute-carried IDs are the stable, Collector-ingestible choice.)

OTelSink takes any object with ``get_logger(name)`` -- inject an SDK
LoggerProvider wired to an OTLP exporter in production, or an in-memory
provider in tests. ``OTelSink.otlp(endpoint)`` builds the OTLP/HTTP
pipeline for the common case.
"""

from __future__ import annotations

import json
from typing import Any

from harness.events import HarnessEvent

__all__ = ["OTelSink"]

# OTel attribute values must be scalars or scalar sequences; nested
# structures (state_snapshot, action_arguments, ...) are JSON-encoded.
_SCALARS = (str, bool, int, float)


def _flatten(payload: dict[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for k, v in payload.items():
        if k == "timestamp":
            continue  # carried as the log record timestamp instead
        if isinstance(v, _SCALARS) or v is None:
            attrs[k] = v if v is not None else ""
        elif isinstance(v, list) and all(isinstance(x, _SCALARS) for x in v):
            attrs[k] = v
        else:
            attrs[k] = json.dumps(v, default=str, sort_keys=True)
    return attrs


class OTelSink:
    """Maps HarnessEvent -> OTel log record. Satisfies the EventSink Protocol."""

    def __init__(
        self,
        logger_provider: Any,
        *,
        logger_name: str = "agents.harness",
    ) -> None:
        self._logger = logger_provider.get_logger(logger_name)
        from opentelemetry._logs import SeverityNumber

        self._info = SeverityNumber.INFO

    @classmethod
    def otlp(cls, endpoint: str | None = None) -> OTelSink:
        """Build an OTLP/HTTP logs pipeline and return a sink over it.

        Requires ``opentelemetry-sdk`` and the OTLP HTTP exporter. The
        endpoint defaults to the standard OTEL env configuration when
        None.
        """
        try:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import (
                OTLPLogExporter,
            )
            from opentelemetry.sdk._logs import LoggerProvider
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ImportError(
                "OTelSink.otlp requires 'agents[otel]' plus opentelemetry-exporter-otlp-proto-http"
            ) from exc
        exporter = OTLPLogExporter(endpoint=endpoint) if endpoint else OTLPLogExporter()
        provider = LoggerProvider()
        provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        return cls(provider)

    def emit(self, event: HarnessEvent) -> None:
        payload = event.model_dump(mode="json")
        ts = int(event.timestamp.timestamp() * 1_000_000_000)
        self._logger.emit(
            timestamp=ts,
            observed_timestamp=ts,
            severity_number=self._info,
            severity_text="INFO",
            body=event.kind,
            attributes=_flatten(payload),
        )
