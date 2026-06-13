"""BL-262 (fifteenth audit): BoundedRedisStore.write is atomic over data + index.

The prior shape set the data key then ZADDed the index in two separate
round trips, so a crash between them stranded a data key with no index
entry (invisible to eviction ordering). The write now allocates the
monotonic score, then SETs the data and ZADDs the index in one MULTI/EXEC.

fakeredis cannot inject a mid-write crash, so this is a behavioural
regression guard: the refactored write keeps the data, the TTL, and the
FIFO eviction ordering intact.
"""

from __future__ import annotations

import pytest

from memory.types import Namespace

fakeredis = pytest.importorskip("fakeredis")

from memory.redis import BoundedRedisStore  # noqa: E402


def _store() -> BoundedRedisStore:
    return BoundedRedisStore(
        Namespace(name="cap", workload="w"), client=fakeredis.aioredis.FakeRedis()
    )


async def test_write_sets_data_and_indexes_for_eviction() -> None:
    store = _store()
    await store.write("a", b"1")
    await store.write("b", b"2")
    assert await store.read("a") == b"1"
    assert await store.read("b") == b"2"

    # Oldest-first eviction: keeping 1 evicts "a" (written first), proving
    # the index entry was created atomically with the data write.
    evicted = await store.evict_to_capacity(1)
    assert evicted == 1
    assert await store.read("a") is None
    assert await store.read("b") == b"2"


async def test_write_honours_ttl_in_the_atomic_pipeline() -> None:
    store = _store()
    await store.write("k", b"v", ttl_seconds=100.0)
    assert await store.read("k") == b"v"
    # The data key carries a positive TTL (set inside the MULTI/EXEC).
    pttl = await store._r.pttl(store._k("k"))
    assert 0 < pttl <= 100_000
