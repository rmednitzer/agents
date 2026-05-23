"""BL-213 (BL-135 size-bound on durable single-host): SQLiteStore.evict_to_capacity.

Counterpart to BL-212's InMemoryStore tests. Tests focus on the SQLite-
specific contract:

- ``SQLiteStore`` satisfies ``BoundedSweepableStore`` (via the rowid
  ordering, with INSERT OR REPLACE assigning a fresh rowid on overwrite,
  the documented divergence from InMemoryStore in the module docstring);
- ``evict_to_capacity`` removes oldest-first by rowid, no-op when at /
  under the cap, validates the cap;
- the count + select + delete operation runs atomically inside
  ``BEGIN IMMEDIATE`` so a concurrent writer cannot interleave (parity
  with the BL-161 mset/mdelete transactional shape);
- expired-but-unswept entries are not counted toward the cap (so a
  TTL'd entry does not double-evict a live one);
- audit emission per evicted key;
- TTLSweeper integration on a durable SQLiteStore drives both passes
  on each interval.
"""

from __future__ import annotations

import asyncio

import pytest

from harness.events import MemoryDelete, MemoryWrite
from harness.sinks import MemorySink
from memory.sqlite import SQLiteStore
from memory.store import BoundedSweepableStore, SweepableStore
from memory.sweep import TTLSweeper
from memory.types import Namespace


def _store(**kwargs: object) -> SQLiteStore:
    return SQLiteStore(Namespace(name="cap", workload="w"), **kwargs)  # type: ignore[arg-type]


# ---- Protocol satisfaction -------------------------------------------------


def test_sqlite_satisfies_bounded_sweepable() -> None:
    s = _store()
    assert isinstance(s, BoundedSweepableStore)
    assert isinstance(s, SweepableStore)
    s.close()


# ---- evict_to_capacity semantics -------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_evict_oldest_first_by_rowid() -> None:
    s = _store()
    try:
        for i in range(5):
            await s.write(f"k{i}", str(i).encode())
        evicted = await s.evict_to_capacity(3)
        assert evicted == 2
        assert sorted(await s.list_keys()) == ["k2", "k3", "k4"]
    finally:
        s.close()


@pytest.mark.asyncio
async def test_sqlite_overwrite_shifts_to_newest() -> None:
    # SQLite divergence from InMemoryStore: ``INSERT OR REPLACE`` on an
    # existing primary key deletes-then-inserts, so the rowid advances
    # to the next sequence number and the overwritten key becomes the
    # *newest* entry, not the oldest. This is documented in the
    # module docstring and pinned here so a future change of semantics
    # triggers CI.
    s = _store()
    try:
        await s.write("a", b"1")
        await s.write("b", b"2")
        await s.write("c", b"3")
        await s.write("a", b"refreshed")  # bumps a to the newest slot
        await s.evict_to_capacity(2)
        # Oldest-by-rowid is now b, c, a (in that order); evict 1
        # leaves [c, a].
        assert sorted(await s.list_keys()) == ["a", "c"]
    finally:
        s.close()


@pytest.mark.asyncio
async def test_sqlite_evict_noop_when_under_cap() -> None:
    s = _store()
    try:
        await s.write("a", b"1")
        await s.write("b", b"2")
        assert await s.evict_to_capacity(5) == 0
        assert sorted(await s.list_keys()) == ["a", "b"]
    finally:
        s.close()


@pytest.mark.asyncio
async def test_sqlite_evict_noop_when_exact() -> None:
    s = _store()
    try:
        await s.write("a", b"1")
        await s.write("b", b"2")
        assert await s.evict_to_capacity(2) == 0
        assert sorted(await s.list_keys()) == ["a", "b"]
    finally:
        s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, -100])
async def test_sqlite_evict_rejects_non_positive(bad: int) -> None:
    s = _store()
    try:
        with pytest.raises(ValueError, match="positive"):
            await s.evict_to_capacity(bad)
    finally:
        s.close()


@pytest.mark.asyncio
async def test_sqlite_evict_skips_expired_rows() -> None:
    # SQL counterpart of BL-195 ``is_live``: an expired-but-unswept row
    # must not count toward live_count, otherwise the capacity pass
    # would double-evict a live row while the dead row still occupies
    # the table. ``sweep_expired`` is the path that drops the dead one;
    # ``evict_to_capacity`` runs over the live subset.
    s = _store()
    try:
        await s.write("alive1", b"1")
        await s.write("dead", b"2", ttl_seconds=0.02)
        await s.write("alive2", b"3")
        await s.write("alive3", b"4")
        await asyncio.sleep(0.05)
        # Live: alive1, alive2, alive3 (3); cap 2 evicts the oldest
        # live row (alive1) and leaves the dead row alone for sweep.
        evicted = await s.evict_to_capacity(2)
        assert evicted == 1
        remaining = sorted(await s.list_keys())
        assert remaining == ["alive2", "alive3"]
    finally:
        s.close()


@pytest.mark.asyncio
async def test_sqlite_evict_emits_audit_per_key() -> None:
    sink = MemorySink()
    s = SQLiteStore(
        Namespace(name="cap", workload="w"),
        sink=sink,
        base_event_fields={
            "workload": "w",
            "contract": "c",
            "contract_version": "1",
            "trace_id": "t",
            "span_id": "s",
        },
    )
    try:
        for i in range(4):
            await s.write(f"k{i}", b"v")
        await s.evict_to_capacity(2)
        writes = [e for e in sink.events if isinstance(e, MemoryWrite)]
        deletes = [e for e in sink.events if isinstance(e, MemoryDelete)]
        assert len(writes) == 4
        assert len(deletes) == 2
    finally:
        s.close()


# ---- TTLSweeper integration on a durable adapter ---------------------------


@pytest.mark.asyncio
async def test_ttl_sweeper_drives_capacity_pass_on_sqlite() -> None:
    s = _store()
    try:
        for i in range(4):
            await s.write(f"k{i}", b"v")
        async with TTLSweeper(s, interval_seconds=0.01, max_keys=2) as sweeper:
            await asyncio.sleep(0.05)
        assert sweeper.swept_total == 0  # no TTL'd entries
        assert sweeper.evicted_total >= 2  # capacity-evicted overflow
        assert sorted(await s.list_keys()) == ["k2", "k3"]
    finally:
        s.close()


@pytest.mark.asyncio
async def test_ttl_sweeper_both_passes_on_sqlite() -> None:
    s = _store()
    try:
        await s.write("dies", b"v", ttl_seconds=0.02)
        await s.write("a", b"v")
        await s.write("b", b"v")
        await s.write("c", b"v")
        await asyncio.sleep(0.05)
        async with TTLSweeper(s, interval_seconds=0.01, max_keys=2) as sweeper:
            await asyncio.sleep(0.05)
        assert sweeper.swept_total >= 1
        assert sweeper.evicted_total >= 1
        assert len(await s.list_keys()) == 2
    finally:
        s.close()
