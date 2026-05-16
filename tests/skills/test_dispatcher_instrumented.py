"""Tests for InstrumentedDispatcher (BL-042)."""

from __future__ import annotations

from typing import Any

import pytest

from harness.events import DispatchObserved
from harness.sinks import MemorySink
from skills.dispatchers.instrumented import InstrumentedDispatcher
from skills.types import SkillMatch


class _Stub:
    def __init__(self, conf: float | None) -> None:
        self.name = "stub"
        self._conf = conf

    async def dispatch(
        self, query: str, *, context: dict[str, Any] | None = None, limit: int = 1
    ) -> list[SkillMatch]:
        if self._conf is None:
            return []
        return [SkillMatch(skill_name="s", confidence=self._conf, rationale="r", dispatcher="stub")]


_BASE = {
    "workload": "w",
    "contract": "c",
    "contract_version": "1",
    "trace_id": "t",
    "span_id": "s",
}


@pytest.mark.asyncio
async def test_passthrough_and_stats_high_confidence() -> None:
    sink = MemorySink()
    d = InstrumentedDispatcher(_Stub(0.9), sink=sink, base_event_fields=_BASE, threshold=0.6)
    matches = await d.dispatch("q")
    assert matches[0].skill_name == "s"
    assert d.stats.calls == 1
    assert d.stats.fallbacks == 0
    assert d.stats.fallback_rate == 0.0
    assert len(sink.events) == 1
    ev = sink.events[0]
    assert isinstance(ev, DispatchObserved)
    assert ev.dispatcher == "stub"
    assert ev.fell_back is False
    assert ev.matched == 1
    assert ev.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_low_confidence_counts_as_fallback() -> None:
    d = InstrumentedDispatcher(_Stub(0.2), threshold=0.6)
    await d.dispatch("q")
    assert d.stats.fallbacks == 1
    assert d.stats.fallback_rate == 1.0


@pytest.mark.asyncio
async def test_no_matches_is_fallback_and_silent_without_base() -> None:
    sink = MemorySink()
    d = InstrumentedDispatcher(_Stub(None), sink=sink)  # no base_event_fields
    await d.dispatch("q")
    assert d.stats.fallbacks == 1
    assert sink.events == []  # silent without base fields
    assert d.name == "instrumented:stub"


@pytest.mark.asyncio
async def test_mean_latency_accumulates() -> None:
    d = InstrumentedDispatcher(_Stub(0.9))
    await d.dispatch("q")
    await d.dispatch("q")
    assert d.stats.calls == 2
    assert d.stats.mean_latency_ms >= 0.0
    assert len(d.stats.latencies_ms) == 2
