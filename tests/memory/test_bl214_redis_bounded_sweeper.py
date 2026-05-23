"""BL-214 (BL-135 size-bound on durable Redis): BoundedRedisStore.

Counterpart to BL-213's SQLite tests, BL-212's InMemoryStore tests.
Tests use ``fakeredis.aioredis.FakeRedis`` (per the existing
``tests/memory/test_redis.py`` pattern) so the suite stays offline
and deterministic.

The Redis case is structurally different from SQLite: there is no
native insertion-order column, so the adapter maintains an auxiliary
sorted-set index outside the namespace prefix (so it cannot collide
with a user key and does not appear in ``list_keys`` / ``scan``).
The tests focus on:

- ``BoundedRedisStore`` satisfies the new Protocol (and the parent
  ``SweepableStore``); the bare ``RedisStore`` does not, by design
  (opt-in subclass);
- ``evict_to_capacity`` evicts oldest-first by index score (ZRANGE
  ascending), with a rewritten key shifting to *newest* (the
  BL-213-style overwrite-shifts-to-newest semantic, matching the
  SQLite divergence);
- the index does not leak into ``list_keys`` / ``scan`` and does not
  collide with a user key named ``__evict_index``;
- ``sweep_expired`` removes index members whose underlying Redis
  data keys have expired (Redis evicts the data key on its own
  schedule; the index needs catch-up);
- expired-but-unswept members are not counted toward the cap (the
  BL-195 read-vs-listing parity in Redis form);
- audit emission per evicted key;
- TTLSweeper integration on a Redis backend drives both passes;
- every keyspace-mutating path on the parent (write / mset / delete /
  mdelete / compare_and_set / compare_and_delete / write_versioned /
  delete_versioned / transact) keeps the index consistent.
"""

from __future__ import annotations

import asyncio

import pytest

from harness.events import MemoryDelete, MemoryWrite
from harness.sinks import MemorySink
from memory.store import BoundedSweepableStore, SweepableStore, TxnDelete, TxnWrite
from memory.sweep import TTLSweeper
from memory.types import Namespace

fakeredis = pytest.importorskip("fakeredis")

from memory.redis import BoundedRedisStore, RedisStore  # noqa: E402


def _store(**kwargs: object) -> BoundedRedisStore:
    client = fakeredis.aioredis.FakeRedis()
    return BoundedRedisStore(
        Namespace(name="cap", workload="w"),
        client=client,
        **kwargs,  # type: ignore[arg-type]
    )


# ---- Protocol satisfaction -------------------------------------------------


def test_bounded_redis_satisfies_bounded_sweepable() -> None:
    s = _store()
    assert isinstance(s, BoundedSweepableStore)
    assert isinstance(s, SweepableStore)


def test_bare_redis_store_does_not_satisfy_bounded_sweepable() -> None:
    # Opt-in: the bare RedisStore intentionally does not implement
    # BoundedSweepableStore, so a TTLSweeper(max_keys=...) load-time
    # isinstance check fails fast on a misconfigured wiring instead
    # of running with a no-op index.
    client = fakeredis.aioredis.FakeRedis()
    s = RedisStore(Namespace(name="cap", workload="w"), client=client)
    assert not isinstance(s, BoundedSweepableStore)
    assert not isinstance(s, SweepableStore)


# ---- evict_to_capacity semantics -------------------------------------------


@pytest.mark.asyncio
async def test_evict_oldest_first_by_index_score() -> None:
    s = _store()
    for i in range(5):
        await s.write(f"k{i}", str(i).encode())
        # A small sleep keeps the ZADD scores monotonic in this test
        # without depending on time.time()'s sub-microsecond resolution
        # (fakeredis honours floating-point scores exactly).
        await asyncio.sleep(0.001)
    evicted = await s.evict_to_capacity(3)
    assert evicted == 2
    assert sorted(await s.list_keys()) == ["k2", "k3", "k4"]


@pytest.mark.asyncio
async def test_rewrite_shifts_key_to_newest() -> None:
    # The Redis divergence (parallel to BL-213's SQLite divergence):
    # a re-ZADD of an existing member updates its score, so a
    # rewritten key orders as *newest* by index. This is consistent
    # with SQLite's INSERT OR REPLACE semantic and diverges from the
    # InMemoryStore first-write FIFO. Pinned by test.
    s = _store()
    await s.write("a", b"1")
    await asyncio.sleep(0.001)
    await s.write("b", b"2")
    await asyncio.sleep(0.001)
    await s.write("c", b"3")
    await asyncio.sleep(0.001)
    await s.write("a", b"refreshed")  # bumps a's index score
    await s.evict_to_capacity(2)
    # Oldest by score after the rewrite: b, c, a (in that order); cap 2
    # evicts b, leaving c and a.
    assert sorted(await s.list_keys()) == ["a", "c"]


@pytest.mark.asyncio
async def test_evict_noop_when_under_cap() -> None:
    s = _store()
    await s.write("a", b"1")
    await s.write("b", b"2")
    assert await s.evict_to_capacity(5) == 0
    assert sorted(await s.list_keys()) == ["a", "b"]


@pytest.mark.asyncio
async def test_evict_noop_when_exact() -> None:
    s = _store()
    await s.write("a", b"1")
    await s.write("b", b"2")
    assert await s.evict_to_capacity(2) == 0
    assert sorted(await s.list_keys()) == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, -100])
async def test_evict_rejects_non_positive(bad: int) -> None:
    s = _store()
    with pytest.raises(ValueError, match="positive"):
        await s.evict_to_capacity(bad)


# ---- Auxiliary index isolation ---------------------------------------------


@pytest.mark.asyncio
async def test_index_does_not_leak_into_list_keys() -> None:
    # The internal index lives outside the namespace prefix
    # (``__evict_index::<namespace>``), so it cannot match the
    # ``<namespace>::*`` filter used by list_keys / scan.
    s = _store()
    await s.write("k1", b"v")
    keys = await s.list_keys()
    assert keys == ["k1"]
    _cur, page = await s.scan(cursor="", prefix="", count=100)
    assert sorted(page) == ["k1"]


@pytest.mark.asyncio
async def test_user_key_named_evict_index_does_not_collide() -> None:
    # A user-written key named ``__evict_index`` would, under a naive
    # design, alias the internal index key (or vice versa). The
    # adapter puts the index outside the namespace prefix so this
    # collision is structurally impossible.
    s = _store()
    await s.write("__evict_index", b"user-data")
    assert await s.read("__evict_index") == b"user-data"
    # And the internal index is still functional: cap=0 not allowed,
    # but cap=1 over the single user key is a no-op.
    assert await s.evict_to_capacity(5) == 0


# ---- sweep_expired (stale-index catch-up) ----------------------------------


@pytest.mark.asyncio
async def test_sweep_expired_cleans_stale_index_entries() -> None:
    # Write a key with a tight TTL, wait for Redis to evict it, then
    # verify sweep_expired removes the (now-stale) index entry. The
    # underlying data key is already gone via Redis's own expiry; the
    # adapter's responsibility is just the auxiliary catch-up.
    s = _store()
    await s.write("alive", b"v")
    await s.write("dies", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    # Now "dies" has been evicted by Redis but its index member
    # remains. sweep_expired should remove it.
    swept = await s.sweep_expired()
    assert swept == 1
    # The index now contains only "alive"; the cap check confirms.
    assert await s.evict_to_capacity(1) == 0


@pytest.mark.asyncio
async def test_sweep_expired_returns_zero_when_index_consistent() -> None:
    s = _store()
    await s.write("a", b"1")
    await s.write("b", b"2")
    assert await s.sweep_expired() == 0


# ---- evict + stale index (BL-195 parity) -----------------------------------


@pytest.mark.asyncio
async def test_evict_skips_expired_index_entries() -> None:
    # A TTL'd key that Redis has already evicted leaves a stale index
    # entry; the capacity pass must not count it toward live_count,
    # otherwise the cap would double-evict a live key while the dead
    # entry still occupies the index. Matches the BL-195 read-vs-
    # listing invariant in Redis form.
    s = _store()
    await s.write("alive1", b"1")
    await s.write("dead", b"2", ttl_seconds=0.02)
    await s.write("alive2", b"3")
    await s.write("alive3", b"4")
    await asyncio.sleep(0.05)
    # Three live, one stale; cap 2 should evict exactly the oldest
    # live (alive1) without double-evicting anything.
    evicted = await s.evict_to_capacity(2)
    assert evicted == 1
    assert sorted(await s.list_keys()) == ["alive2", "alive3"]


# ---- Audit emission --------------------------------------------------------


@pytest.mark.asyncio
async def test_evict_emits_audit_per_key() -> None:
    sink = MemorySink()
    client = fakeredis.aioredis.FakeRedis()
    s = BoundedRedisStore(
        Namespace(name="cap", workload="w"),
        client=client,
        sink=sink,
        base_event_fields={
            "workload": "w",
            "contract": "c",
            "contract_version": "1",
            "trace_id": "t",
            "span_id": "s",
        },
    )
    for i in range(4):
        await s.write(f"k{i}", b"v")
        await asyncio.sleep(0.001)
    await s.evict_to_capacity(2)
    writes = [e for e in sink.events if isinstance(e, MemoryWrite)]
    deletes = [e for e in sink.events if isinstance(e, MemoryDelete)]
    assert len(writes) == 4
    assert len(deletes) == 2


# ---- TTLSweeper integration ------------------------------------------------


@pytest.mark.asyncio
async def test_ttl_sweeper_drives_capacity_pass_on_redis() -> None:
    s = _store()
    for i in range(4):
        await s.write(f"k{i}", b"v")
        await asyncio.sleep(0.001)
    async with TTLSweeper(s, interval_seconds=0.01, max_keys=2) as sweeper:
        await asyncio.sleep(0.05)
    # swept_total counts stale-index cleanups (zero here, no TTL).
    # evicted_total counts capacity evictions (>=2 expected).
    assert sweeper.evicted_total >= 2
    assert sorted(await s.list_keys()) == ["k2", "k3"]


@pytest.mark.asyncio
async def test_ttl_sweeper_both_passes_on_redis() -> None:
    # 1 expiring + 3 non-expiring above a cap of 2. The expiring one
    # is auto-evicted by Redis; sweep_expired removes its stale index
    # entry; the capacity pass evicts the oldest of the three live
    # entries to land at the cap.
    s = _store()
    await s.write("dies", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.001)
    await s.write("a", b"v")
    await asyncio.sleep(0.001)
    await s.write("b", b"v")
    await asyncio.sleep(0.001)
    await s.write("c", b"v")
    await asyncio.sleep(0.05)  # let Redis evict "dies"
    async with TTLSweeper(s, interval_seconds=0.01, max_keys=2) as sweeper:
        await asyncio.sleep(0.05)
    assert sweeper.swept_total >= 1
    assert sweeper.evicted_total >= 1
    assert len(await s.list_keys()) == 2


# ---- Index consistency across every mutation path --------------------------


@pytest.mark.asyncio
async def test_index_tracks_mset_mdelete() -> None:
    s = _store()
    await s.mset({"a": b"1", "b": b"2", "c": b"3"})
    await s.mdelete(["b"])
    # cap=1 should evict "a" (the older of the two remaining live
    # entries; mset writes all at the same timestamp but ZADD with
    # identical scores preserves member ordering deterministically
    # for fakeredis -- we accept either "a" or "c" as the evicted one
    # by checking only the count and the index size).
    assert await s.evict_to_capacity(1) == 1
    assert len(await s.list_keys()) == 1


@pytest.mark.asyncio
async def test_index_tracks_compare_and_set() -> None:
    s = _store()
    await s.write("k", b"v0")
    await asyncio.sleep(0.001)
    ok = await s.compare_and_set("k", b"v0", b"v1")
    assert ok
    # CAS-updated key should still be in the index; cap=0 not allowed
    # but cap=1 means no eviction since only one key is present.
    assert await s.evict_to_capacity(1) == 0


@pytest.mark.asyncio
async def test_index_tracks_compare_and_delete() -> None:
    s = _store()
    await s.write("k", b"v")
    ok = await s.compare_and_delete("k", b"v")
    assert ok
    # The index should now be empty; cap=1 is a no-op.
    assert await s.evict_to_capacity(1) == 0
    # And the key is gone.
    assert await s.read("k") is None


@pytest.mark.asyncio
async def test_index_tracks_versioned_write_and_delete() -> None:
    s = _store()
    token1 = await s.write_versioned("k", b"v1")
    assert token1 is not None
    token2 = await s.write_versioned("k", b"v2", expected_version=token1)
    assert token2 is not None
    ok = await s.delete_versioned("k", token2)
    assert ok
    assert await s.evict_to_capacity(1) == 0
    assert await s.read("k") is None


@pytest.mark.asyncio
async def test_index_tracks_transact() -> None:
    s = _store()
    # First write so we have version tokens to delete with.
    t_a = await s.write_versioned("a", b"1")
    assert t_a is not None
    # Transact: write b/c, delete a.
    out = await s.transact(
        writes={"b": TxnWrite(value=b"2"), "c": TxnWrite(value=b"3")},
        deletes={"a": TxnDelete(expected_version=t_a)},
    )
    assert out is not None
    assert sorted(await s.list_keys()) == ["b", "c"]
    # Index has b and c; cap=1 evicts the older.
    assert await s.evict_to_capacity(1) == 1
    assert len(await s.list_keys()) == 1
