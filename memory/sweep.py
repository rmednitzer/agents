"""Active TTL sweep background task (BL-080, ADR 0007).

Lazy expiry (drop-on-access) is sufficient for correctness; the
InMemoryStore and most backends rely on it. But a keyspace of
write-once, never-read, TTL'd entries grows unbounded under lazy expiry
alone. TTLSweeper drives a SweepableStore's ``sweep_expired`` on a fixed
interval so that space is reclaimed without an access.

The sweeper is opt-in and adapter-agnostic: it depends only on the
SweepableStore Protocol. It is cancellation-safe and idempotent to
stop. A transient exception from ``sweep_expired`` is caught and
recorded (`BL-199`, BL-189 class extension) so a single backend blip
does not silently kill the loop for the rest of the process lifetime;
the next interval retries normally.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from typing import cast

from memory.store import BoundedSweepableStore, SweepableStore

__all__ = ["TTLSweeper"]


class TTLSweeper:
    """Periodically calls ``store.sweep_expired()`` until stopped.

    Usage::

        sweeper = TTLSweeper(store, interval_seconds=60)
        sweeper.start()
        ...
        await sweeper.aclose()

    or as an async context manager::

        async with TTLSweeper(store, interval_seconds=60):
            ...

    With ``max_keys`` set, the sweeper additionally calls
    ``store.evict_to_capacity(max_keys)`` after the age-only sweep on
    each interval (`BL-212`, the size-bound half of `BL-135`). The
    store must implement ``BoundedSweepableStore``; a non-bounded
    store with ``max_keys`` set raises ``TypeError`` at construction
    so the configuration error surfaces at load time, not mid-run
    (ADR 0007). ``evicted_total`` accumulates the number of entries
    removed by the capacity pass for observability and tests; it is
    distinct from ``swept_total`` (the age-only counter) so an
    operator can tell whether growth is TTL-driven or write-rate-driven.

    ``swept_total`` accumulates the number of entries removed across all
    age-only sweeps for observability and tests. ``failures_total``
    accumulates the number of consecutive maintenance attempts (sweep
    or capacity pass) that raised so an operator can detect a
    persistently broken backend (`BL-199`); the counter resets to zero
    on the next fully-successful interval so a transient blip
    self-heals without manual intervention. ``last_error`` carries the
    most recent exception (or ``None`` if the last interval
    succeeded), so a caller can introspect the failure without parsing
    logs.
    """

    def __init__(
        self,
        store: SweepableStore,
        *,
        interval_seconds: float,
        max_keys: int | None = None,
    ) -> None:
        # BL-232: ``<= 0`` alone has a NaN / +inf hole (``NaN <= 0`` and
        # ``+inf <= 0`` are both False). A NaN interval slipped through
        # and ``asyncio.wait_for(..., timeout=NaN)`` raises TimeoutError
        # immediately, turning the loop into a no-delay busy-sweep that
        # hammers the backend. The ``math.isfinite`` conjunct closes it
        # (the MCPServerSpec.timeout_seconds twin; BL-197 / BL-159 class
        # on the config boundary).
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("interval_seconds must be a positive finite number")
        if max_keys is not None:
            if max_keys <= 0:
                raise ValueError("max_keys must be positive")
            if not isinstance(store, BoundedSweepableStore):
                raise TypeError(
                    "max_keys requires a BoundedSweepableStore; "
                    f"{type(store).__name__} does not implement evict_to_capacity"
                )
        self._store = store
        self._interval = interval_seconds
        self._max_keys = max_keys
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.swept_total = 0
        self.evicted_total = 0
        # BL-199: surface the failure path without killing the loop.
        # ``failures_total`` is the consecutive-failure counter (reset
        # on the next success); ``last_error`` is the most recent
        # exception or None. Together they let an operator detect a
        # persistently broken backend; the loop itself is robust to
        # transients.
        self.failures_total = 0
        self.last_error: BaseException | None = None

    def start(self) -> None:
        """Start the background sweep loop. Idempotent."""
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            if self._stop.is_set():
                break
            try:
                self.swept_total += await self._store.sweep_expired()
                if self._max_keys is not None:
                    # BL-212: capacity pass after age-only sweep. The
                    # isinstance check at __init__ guarantees the
                    # method exists; runtime cast keeps mypy happy
                    # without a second narrowing pass per interval.
                    bounded = cast(BoundedSweepableStore, self._store)
                    self.evicted_total += await bounded.evict_to_capacity(self._max_keys)
            except asyncio.CancelledError:
                # Cancellation is the documented stop signal; do not
                # swallow it so ``aclose`` can complete.
                raise
            except Exception as exc:
                # A transient backend error (network blip on Redis /
                # DynamoDB / S3 Sweepable, throttling, etc.) must not
                # silently kill the loop for the rest of the process
                # lifetime (BL-199, BL-189 class). Record the failure
                # and continue at the next interval; an operator
                # introspecting ``failures_total`` / ``last_error``
                # can detect a persistent backend break without
                # parsing logs.
                self.failures_total += 1
                self.last_error = exc
            else:
                # Successful sweep: reset the consecutive-failure
                # counter so a transient blip self-heals.
                self.failures_total = 0
                self.last_error = None

    async def aclose(self) -> None:
        """Stop the loop and await task teardown. Idempotent."""
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def __aenter__(self) -> TTLSweeper:
        self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
