"""Event sinks for the harness.

A sink receives HarnessEvent instances. The harness emits to one sink;
use MultiSink to fan out to several.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from harness.events import HarnessEvent

__all__ = [
    "EventSink",
    "JsonlSink",
    "MemorySink",
    "MultiSink",
    "NullSink",
]


@runtime_checkable
class EventSink(Protocol):
    """Receives events emitted by the harness."""

    def emit(self, event: HarnessEvent) -> None: ...


class NullSink:
    """Discards events. The default when no sink is provided."""

    def emit(self, event: HarnessEvent) -> None:
        return None


class MemorySink:
    """Buffers events in memory. For tests and short-lived inspection."""

    def __init__(self) -> None:
        self.events: list[HarnessEvent] = []

    def emit(self, event: HarnessEvent) -> None:
        self.events.append(event)


class JsonlSink:
    """Appends events to a JSONL file. For local dev and audit packs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: HarnessEvent) -> None:
        # BL-219: pin UTF-8 explicitly so a non-default platform locale
        # (Windows cp1252, C locale ASCII) cannot mis-encode a JSONL
        # event carrying non-ASCII content (a unicode prompt template,
        # a localised error message, a redacted span containing high
        # bytes). The BL-218 read-side standard applied to the write
        # side; the project's explicit-UTF-8 convention now spans both
        # legs of every file I/O.
        with self.path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")


class MultiSink:
    """Fan-out: emit each event to all wrapped sinks, in order.

    Per-sink failure is contained (`BL-223`, BL-222 class extension on
    the audit fan-out side): a single sink raising ``Exception``
    (e.g., a flaky OTel exporter, a disk-full ``JsonlSink``, a
    third-party sink with a transient network error) does NOT prevent
    downstream sinks from receiving the event. The audit-vs-raise
    parity invariant (BL-202 / BL-167: every state-affecting raise
    has a matching audit event) requires the fan-out to deliver
    maximally, not skip on the first failure.

    ``BaseException`` (KeyboardInterrupt, SystemExit,
    asyncio.CancelledError) is NOT contained: those are authoritative
    termination signals and must reach the caller, parity with the
    runtime's BL-165 "do not reinterpret cancellation as a pause"
    invariant.
    """

    def __init__(self, *sinks: EventSink) -> None:
        self.sinks: tuple[EventSink, ...] = sinks

    def emit(self, event: HarnessEvent) -> None:
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception:
                # Per-sink containment (`BL-223`): an Exception from
                # one sink must not block the rest of the fan-out, or
                # an OTLP sink with a transient network failure could
                # silently swallow a BudgetExceededEvent or
                # GovernanceViolated from the in-memory / JSONL sinks
                # paired with it. BaseException (the line above only
                # catches Exception) still propagates so terminal
                # signals are not swallowed.
                continue
