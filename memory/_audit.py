"""Shared audit-event emission for MemoryStore adapters (BL-040).

Every adapter that supports the optional ``sink`` /
``base_event_fields`` audit surface uses ``MemoryAudit`` so the
convention is defined once: validate the base fields at construction
(a partial dict is a load-time error, not a mid-run ValidationError),
and emit only when base fields were supplied (silent standalone use,
matching BudgetTracker / HarnessToolGuard).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from harness.events import MemoryDelete, MemoryRead, MemoryWrite
from harness.sinks import EventSink, NullSink

__all__ = ["MemoryAudit"]

# HarnessEvent base fields the caller must supply; timestamp/kind are
# set per emit and parent_span_id is optional.
_REQUIRED_BASE_FIELDS = frozenset(
    {"workload", "contract", "contract_version", "trace_id", "span_id"}
)


class MemoryAudit:
    """Holds the sink + base fields and emits memory operation events."""

    __slots__ = ("_base", "_namespace", "_sink")

    def __init__(
        self,
        namespace: str,
        sink: EventSink | None,
        base_event_fields: dict[str, Any] | None,
    ) -> None:
        base = base_event_fields if base_event_fields is not None else {}
        if base:
            missing = _REQUIRED_BASE_FIELDS - base.keys()
            if missing:
                raise ValueError(
                    "base_event_fields missing required keys: "
                    f"{sorted(missing)} (a partial dict would fail mid-run "
                    "on the first emitted event)"
                )
        self._namespace = namespace
        self._sink: EventSink = sink if sink is not None else NullSink()
        self._base = base

    @property
    def enabled(self) -> bool:
        return bool(self._base)

    def read(self, key: str, *, hit: bool) -> None:
        if self._base:
            self._sink.emit(
                MemoryRead(
                    timestamp=datetime.now(UTC),
                    namespace=self._namespace,
                    key=key,
                    hit=hit,
                    **self._base,
                )
            )

    def write(self, key: str, *, value_bytes: int, ttl_seconds: float | None) -> None:
        if self._base:
            self._sink.emit(
                MemoryWrite(
                    timestamp=datetime.now(UTC),
                    namespace=self._namespace,
                    key=key,
                    value_bytes=value_bytes,
                    ttl_seconds=ttl_seconds,
                    **self._base,
                )
            )

    def delete(self, key: str, *, existed: bool) -> None:
        if self._base:
            self._sink.emit(
                MemoryDelete(
                    timestamp=datetime.now(UTC),
                    namespace=self._namespace,
                    key=key,
                    existed=existed,
                    **self._base,
                )
            )
