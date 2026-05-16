"""Tests for memory.redis.RedisStore (BL-030), via fakeredis."""

from __future__ import annotations

import asyncio

import pytest

from harness.sinks import MemorySink
from memory.redis import RedisStore
from memory.store import (
    BatchMemoryStore,
    CASMemoryStore,
    ContentAddressableStore,
    MemoryStore,
    ScannableStore,
)
from memory.types import Namespace

fakeredis = pytest.importorskip("fakeredis")


def _store(name: str = "ns", retention: float | None = None, **kw: object) -> RedisStore:
    client = fakeredis.aioredis.FakeRedis()
    ns = Namespace(name=name, workload="w", retention_seconds=retention)
    return RedisStore(ns, client=client, **kw)


@pytest.mark.asyncio
async def test_satisfies_protocols() -> None:
    s = _store()
    assert isinstance(s, MemoryStore)
    assert isinstance(s, BatchMemoryStore)
    assert isinstance(s, ScannableStore)
    assert isinstance(s, ContentAddressableStore)
    assert isinstance(s, CASMemoryStore)


@pytest.mark.asyncio
async def test_roundtrip_and_native_ttl() -> None:
    s = _store()
    await s.write("k", b"v")
    assert await s.read("k") == b"v"
    await s.write("t", b"v", ttl_seconds=0.05)
    await asyncio.sleep(0.12)
    assert await s.read("t") is None  # Redis evicted it natively
    await s.delete("k")
    assert await s.read("k") is None


@pytest.mark.asyncio
async def test_namespace_isolation_via_prefix() -> None:
    client = fakeredis.aioredis.FakeRedis()
    a = RedisStore(Namespace(name="alpha", workload="w"), client=client)
    b = RedisStore(Namespace(name="beta", workload="w"), client=client)
    await a.write("k", b"a")
    await b.write("k", b"b")
    assert await a.read("k") == b"a"
    assert await b.read("k") == b"b"
    assert await a.list_keys() == ["k"]


@pytest.mark.asyncio
async def test_batch_scan_content() -> None:
    s = _store()
    await s.mset({"k1": b"1", "k2": b"2"})
    assert await s.mget(["k1", "x", "k2"]) == [b"1", None, b"2"]
    await s.mdelete(["k1"])
    assert await s.list_keys() == ["k2"]
    key = await s.write_content(b"blob")
    assert await s.read(key) == b"blob"

    all_keys: list[str] = []
    cursor = ""
    while True:
        cursor, page = await s.scan(cursor=cursor, count=1)
        all_keys.extend(page)
        if not cursor:
            break
    assert set(all_keys) == {"k2", key}


@pytest.mark.asyncio
async def test_cas_watch_multi() -> None:
    s = _store()
    assert await s.compare_and_set("c", None, b"v1") is True
    assert await s.compare_and_set("c", None, b"v2") is False
    assert await s.compare_and_set("c", b"v1", b"v2") is True
    assert await s.read("c") == b"v2"
    assert await s.compare_and_delete("c", b"wrong") is False
    assert await s.compare_and_delete("c", b"v2") is True
    assert await s.read("c") is None


@pytest.mark.asyncio
async def test_sub_millisecond_ttl_does_not_break_px() -> None:
    """Regression: ttl in (0, 0.001) must not produce Redis PX=0."""
    s = _store()
    await s.write("k", b"v", ttl_seconds=0.0004)  # int(*1000) == 0 -> guard to 1
    await s.mset({"m": b"v"}, ttl_seconds=0.0004)
    assert await s.compare_and_set("c", None, b"v", ttl_seconds=0.0004) is True


@pytest.mark.asyncio
async def test_audit_events() -> None:
    base = {
        "workload": "w",
        "contract": "c",
        "contract_version": "1",
        "trace_id": "t",
        "span_id": "s",
    }
    sink = MemorySink()
    s = _store(sink=sink, base_event_fields=base)
    await s.write("k", b"v")
    await s.read("k")
    await s.delete("k")
    assert [e.kind for e in sink.events] == [
        "memory_write",
        "memory_read",
        "memory_delete",
    ]
