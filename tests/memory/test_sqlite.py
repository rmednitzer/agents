"""Tests for memory.sqlite.SQLiteStore (BL-031)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from harness.sinks import MemorySink
from memory.errors import NamespaceViolation
from memory.sqlite import SQLiteStore
from memory.store import (
    BatchMemoryStore,
    CASMemoryStore,
    ContentAddressableStore,
    MemoryStore,
    ScannableStore,
    SweepableStore,
)
from memory.types import Namespace


def _ns(name: str = "ns", retention: float | None = None) -> Namespace:
    return Namespace(name=name, workload="w", retention_seconds=retention)


def _store(retention: float | None = None) -> SQLiteStore:
    return SQLiteStore(_ns(retention=retention))


@pytest.mark.asyncio
async def test_satisfies_all_protocols() -> None:
    s = _store()
    assert isinstance(s, MemoryStore)
    assert isinstance(s, BatchMemoryStore)
    assert isinstance(s, ScannableStore)
    assert isinstance(s, ContentAddressableStore)
    assert isinstance(s, CASMemoryStore)
    assert isinstance(s, SweepableStore)


@pytest.mark.asyncio
async def test_write_read_delete_roundtrip() -> None:
    s = _store()
    await s.write("k", b"\x00bin\xff")
    assert await s.read("k") == b"\x00bin\xff"
    await s.delete("k")
    assert await s.read("k") is None
    await s.delete("k")  # idempotent


@pytest.mark.asyncio
async def test_ttl_expires_lazily() -> None:
    s = _store()
    await s.write("k", b"v", ttl_seconds=0.05)
    assert await s.read("k") == b"v"
    await asyncio.sleep(0.1)
    assert await s.read("k") is None


@pytest.mark.asyncio
async def test_namespace_default_ttl_and_list_prefix() -> None:
    s = _store(retention=0.05)
    await s.write("a-1", b"v")
    await s.write("a-2", b"v")
    await s.write("b-1", b"v")
    assert await s.list_keys("a-") == ["a-1", "a-2"]
    await asyncio.sleep(0.1)
    assert await s.list_keys() == []


@pytest.mark.asyncio
async def test_invalid_keys_rejected() -> None:
    s = _store()
    with pytest.raises(NamespaceViolation):
        await s.write("with::sep", b"v")
    with pytest.raises(NamespaceViolation):
        await s.read("../escape")


@pytest.mark.asyncio
async def test_batch_scan_content_cas_sweep() -> None:
    s = _store()
    await s.mset({"k1": b"1", "k2": b"2"})
    assert await s.mget(["k1", "missing", "k2"]) == [b"1", None, b"2"]
    await s.mdelete(["k1"])
    assert await s.list_keys() == ["k2"]

    key = await s.write_content(b"blob")
    assert await s.read(key) == b"blob"

    assert await s.compare_and_set("c", None, b"v1") is True
    assert await s.compare_and_set("c", None, b"v2") is False
    assert await s.compare_and_delete("c", b"v1") is True

    await s.write("t", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    assert await s.sweep_expired() >= 1


@pytest.mark.asyncio
async def test_scan_paging() -> None:
    s = _store()
    await s.mset({f"k{i:02d}": b"v" for i in range(7)})
    seen: list[str] = []
    cursor = ""
    while True:
        cursor, page = await s.scan(cursor=cursor, count=3)
        seen.extend(page)
        if not cursor:
            break
    assert seen == sorted(f"k{i:02d}" for i in range(7))


@pytest.mark.asyncio
async def test_persists_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    s1 = SQLiteStore(_ns(), db)
    await s1.write("durable", b"value")
    s1.close()

    s2 = SQLiteStore(_ns(), db)
    assert await s2.read("durable") == b"value"
    s2.close()


@pytest.mark.asyncio
async def test_namespaces_isolated_in_one_db(tmp_path: Path) -> None:
    db = tmp_path / "shared.db"
    a = SQLiteStore(_ns("alpha"), db)
    b = SQLiteStore(_ns("beta"), db)
    await a.write("k", b"from-a")
    await b.write("k", b"from-b")
    assert await a.read("k") == b"from-a"
    assert await b.read("k") == b"from-b"
    a.close()
    b.close()


@pytest.mark.asyncio
async def test_audit_events_emitted() -> None:
    sink = MemorySink()
    base = {
        "workload": "w",
        "contract": "c",
        "contract_version": "1.0",
        "trace_id": "t",
        "span_id": "s",
    }
    s = SQLiteStore(_ns(), sink=sink, base_event_fields=base)
    await s.write("k", b"v")
    await s.read("k")
    await s.delete("k")
    kinds = [e.kind for e in sink.events]
    assert kinds == ["memory_write", "memory_read", "memory_delete"]
