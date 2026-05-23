"""InstrumentedDispatcher: latency + fallback-rate observability (BL-042)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from harness.events import DispatchObserved
from harness.sinks import EventSink, NullSink
from skills.dispatcher import Dispatcher
from skills.types import SkillMatch

__all__ = ["DispatchStats", "InstrumentedDispatcher"]


@dataclass
class DispatchStats:
    """In-process rollup, complementary to the emitted events.

    Latency samples are kept raw so the caller can compute any
    percentile; the OTel sink (BL-041) is the path to Grafana
    histograms. Token consumption is intentionally not tracked here:
    the pure Dispatcher Protocol does not expose it; that accounting
    belongs to the BudgetTracker at the runtime layer.
    """

    calls: int = 0
    fallbacks: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def fallback_rate(self) -> float:
        return self.fallbacks / self.calls if self.calls else 0.0

    @property
    def mean_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0


class InstrumentedDispatcher:
    """Wraps any Dispatcher; times it and emits DispatchObserved.

    ``threshold`` defines a fallback: a call whose top confidence is
    below it (or that returned nothing) is counted as a fallback, the
    signal RoutingChainDispatcher users care about. The wrapper is
    transparent -- it returns the inner dispatcher's matches unchanged.
    """

    def __init__(
        self,
        inner: Dispatcher,
        *,
        sink: EventSink | None = None,
        base_event_fields: dict[str, Any] | None = None,
        threshold: float = 0.6,
    ) -> None:
        self._inner = inner
        self._sink: EventSink = sink if sink is not None else NullSink()
        self._base = base_event_fields if base_event_fields is not None else {}
        self._threshold = threshold
        self.stats = DispatchStats()

    @property
    def name(self) -> str:
        return f"instrumented:{self._inner.name}"

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        start = time.perf_counter()
        matches: list[SkillMatch] = []
        # `try/finally` so a failed inner dispatch (e.g., a
        # `DispatchError` from an LLM-backed inner, an `asyncio.
        # CancelledError`, etc.) is still observable: `calls` and
        # latency are recorded and the event is emitted with
        # ``fell_back=True`` / ``matched=0`` (`BL-207`, BL-189 / BL-167
        # class extension). Without this, a workload monitoring
        # `fallback_rate` to detect routing-health sees `0/0` no matter
        # how many dispatch attempts crash, exactly the opposite of
        # what observability is supposed to surface.
        try:
            matches = await self._inner.dispatch(query, context=context, limit=limit)
        finally:
            latency_ms = (time.perf_counter() - start) * 1000.0
            top = matches[0].confidence if matches else 0.0
            fell_back = top < self._threshold
            self.stats.calls += 1
            self.stats.latencies_ms.append(latency_ms)
            if fell_back:
                self.stats.fallbacks += 1

            if self._base:
                self._sink.emit(
                    DispatchObserved(
                        timestamp=datetime.now(UTC),
                        dispatcher=self._inner.name,
                        latency_ms=latency_ms,
                        matched=len(matches),
                        top_confidence=top,
                        fell_back=fell_back,
                        **self._base,
                    )
                )
        return matches
