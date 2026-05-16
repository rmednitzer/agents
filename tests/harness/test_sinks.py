"""Tests for harness.sinks."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from harness.events import ContractStarted
from harness.sinks import (
    EventSink,
    JsonlSink,
    MemorySink,
    MultiSink,
    NullSink,
)


def _event() -> ContractStarted:
    return ContractStarted(
        timestamp=datetime.now(UTC),
        workload="w",
        contract="c",
        contract_version="0.1.0",
        trace_id="t",
        span_id="s",
    )


def test_null_sink_discards() -> None:
    sink: EventSink = NullSink()
    sink.emit(_event())  # no exception, no return


def test_memory_sink_buffers() -> None:
    sink = MemorySink()
    e = _event()
    sink.emit(e)
    sink.emit(e)
    assert len(sink.events) == 2
    assert sink.events[0] is e


def test_jsonl_sink_appends_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path)
    sink.emit(_event())
    sink.emit(_event())
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        assert parsed["kind"] == "contract_started"


def test_jsonl_sink_creates_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "events.jsonl"
    JsonlSink(path)
    assert path.parent.is_dir()


def test_multi_sink_fans_out() -> None:
    a = MemorySink()
    b = MemorySink()
    multi = MultiSink(a, b)
    multi.emit(_event())
    assert len(a.events) == 1
    assert len(b.events) == 1


def test_all_sinks_satisfy_protocol() -> None:
    for sink in (NullSink(), MemorySink(), MultiSink()):
        assert isinstance(sink, EventSink)
