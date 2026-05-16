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
        with self.path.open("a") as f:
            f.write(event.model_dump_json() + "\n")


class MultiSink:
    """Fan-out: emit each event to all wrapped sinks, in order."""

    def __init__(self, *sinks: EventSink) -> None:
        self.sinks: tuple[EventSink, ...] = sinks

    def emit(self, event: HarnessEvent) -> None:
        for sink in self.sinks:
            sink.emit(event)
