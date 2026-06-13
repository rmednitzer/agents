"""BL-259 (fifteenth audit): SQLite samples the TTL clock after BEGIN IMMEDIATE.

The transactional write paths (transact / compare_and_set /
write_versioned / mset) computed ``expires_at = now + ttl`` from a ``now``
sampled BEFORE ``BEGIN IMMEDIATE``. Under cross-instance write contention
``BEGIN IMMEDIATE`` can block up to the sqlite3 busy timeout, so a stale
``now`` could write a short-TTL row already-expired. ``evict_to_capacity``
already sampled after BEGIN IMMEDIATE (with a comment); this aligns the
others.

The test wraps the connection so BEGIN IMMEDIATE advances a fake clock,
simulating that blocking window, and asserts the stored ``expires_at``
reflects the post-BEGIN time.
"""

from __future__ import annotations

from typing import Any

import pytest

import memory.sqlite as sqlite_mod
from memory.sqlite import SQLiteStore
from memory.store import TxnWrite
from memory.types import Namespace


class _ClockBumpConn:
    """Delegates to a real sqlite3 connection, advancing ``clock`` by
    ``bump`` seconds when ``BEGIN IMMEDIATE`` executes (the contended
    write-lock window)."""

    def __init__(self, real: Any, clock: dict[str, float], bump: float) -> None:
        self._real = real
        self._clock = clock
        self._bump = bump

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if isinstance(sql, str) and sql.startswith("BEGIN IMMEDIATE"):
            self._clock["t"] += self._bump
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


async def test_transact_samples_ttl_after_begin_immediate(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(sqlite_mod.time, "time", lambda: clock["t"])
    store = SQLiteStore(Namespace(name="t", workload="w"))
    real = store._conn
    store._conn = _ClockBumpConn(real, clock, bump=100.0)  # type: ignore[assignment]

    out = await store.transact(writes={"k": TxnWrite(value=b"v", ttl_seconds=50.0)})
    assert out is not None

    row = real.execute(f'SELECT expires_at FROM "{store._table}" WHERE key=?', ("k",)).fetchone()
    # BEGIN IMMEDIATE advanced the clock 1000 -> 1100, and `now` is now
    # sampled there, so expires_at = 1100 + 50 = 1150, not the pre-BEGIN
    # 1000 + 50 = 1050.
    assert row[0] == pytest.approx(1150.0)
    store.close()


async def test_compare_and_set_samples_ttl_after_begin_immediate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"t": 2000.0}
    monkeypatch.setattr(sqlite_mod.time, "time", lambda: clock["t"])
    store = SQLiteStore(Namespace(name="t2", workload="w"))
    real = store._conn
    store._conn = _ClockBumpConn(real, clock, bump=100.0)  # type: ignore[assignment]

    ok = await store.compare_and_set("k", None, b"v", ttl_seconds=50.0)
    assert ok is True
    row = real.execute(f'SELECT expires_at FROM "{store._table}" WHERE key=?', ("k",)).fetchone()
    assert row[0] == pytest.approx(2150.0)
    store.close()
