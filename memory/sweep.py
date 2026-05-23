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

from memory.store import SweepableStore

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

    ``swept_total`` accumulates the number of entries removed across all
    sweeps for observability and tests. ``failures_total`` accumulates
    the number of consecutive sweep attempts that raised so an
    operator can detect a persistently broken backend (`BL-199`); the
    counter resets to zero on the next successful sweep so a transient
    blip self-heals without manual intervention. ``last_error`` carries
    the most recent exception (or ``None`` if the last sweep
    succeeded), so a caller can introspect the failure without parsing
    logs.
    """

    def __init__(self, store: SweepableStore, *, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._store = store
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.swept_total = 0
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
