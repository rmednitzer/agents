"""Tests for memory.inmemory.InMemoryStore."""

from __future__ import annotations

import asyncio

import pytest

from memory.errors import NamespaceViolation
from memory.inmemory import InMemoryStore
from memory.store import MemoryStore
from memory.types import Namespace


def _ns(
    name: str = "test", workload: str = "w", retention_seconds: float | None = None
) -> Namespace:
    return Namespace(name=name, workload=workload, retention_seconds=retention_seconds)


@pytest.mark.asyncio
async def test_write_then_read() -> None:
    store = InMemoryStore(_ns())
    await store.write("k", b"hello")
    assert await store.read("k") == b"hello"


@pytest.mark.asyncio
async def test_read_nonexistent_returns_none() -> None:
    store = InMemoryStore(_ns())
    assert await store.read("missing") is None


@pytest.mark.asyncio
async def test_delete_then_read_returns_none() -> None:
    store = InMemoryStore(_ns())
    await store.write("k", b"v")
    await store.delete("k")
    assert await store.read("k") is None


@pytest.mark.asyncio
async def test_delete_nonexistent_is_idempotent() -> None:
    store = InMemoryStore(_ns())
    await store.delete("never-existed")  # no exception


@pytest.mark.asyncio
async def test_list_keys_empty_store() -> None:
    store = InMemoryStore(_ns())
    assert await store.list_keys() == []


@pytest.mark.asyncio
async def test_list_keys_sorted_lexicographically() -> None:
    store = InMemoryStore(_ns())
    for k in ("c", "a", "b"):
        await store.write(k, b"v")
    assert await store.list_keys() == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_list_keys_with_prefix() -> None:
    store = InMemoryStore(_ns())
    for k in ("entry-1", "entry-2", "other"):
        await store.write(k, b"v")
    assert await store.list_keys("entry-") == ["entry-1", "entry-2"]


@pytest.mark.asyncio
async def test_overwrite_replaces_value() -> None:
    store = InMemoryStore(_ns())
    await store.write("k", b"v1")
    await store.write("k", b"v2")
    assert await store.read("k") == b"v2"


@pytest.mark.asyncio
async def test_explicit_ttl_expires() -> None:
    store = InMemoryStore(_ns())
    await store.write("k", b"v", ttl_seconds=0.05)
    assert await store.read("k") == b"v"
    await asyncio.sleep(0.1)
    assert await store.read("k") is None


@pytest.mark.asyncio
async def test_no_ttl_persists() -> None:
    store = InMemoryStore(_ns())
    await store.write("k", b"v")
    await asyncio.sleep(0.05)
    assert await store.read("k") == b"v"


@pytest.mark.asyncio
async def test_namespace_default_ttl_applied() -> None:
    store = InMemoryStore(_ns(retention_seconds=0.05))
    await store.write("k", b"v")
    await asyncio.sleep(0.1)
    assert await store.read("k") is None


@pytest.mark.asyncio
async def test_explicit_ttl_overrides_namespace_default() -> None:
    store = InMemoryStore(_ns(retention_seconds=0.05))
    await store.write("k", b"v", ttl_seconds=1.0)  # explicit longer TTL
    await asyncio.sleep(0.1)
    assert await store.read("k") == b"v"


@pytest.mark.asyncio
async def test_list_keys_excludes_expired() -> None:
    store = InMemoryStore(_ns())
    await store.write("permanent", b"v")
    await store.write("temporary", b"v", ttl_seconds=0.05)
    await asyncio.sleep(0.1)
    assert await store.list_keys() == ["permanent"]


@pytest.mark.asyncio
async def test_invalid_key_rejected_on_write() -> None:
    store = InMemoryStore(_ns())
    with pytest.raises(NamespaceViolation):
        await store.write("with::sep", b"v")


@pytest.mark.asyncio
async def test_invalid_key_rejected_on_read() -> None:
    store = InMemoryStore(_ns())
    with pytest.raises(NamespaceViolation):
        await store.read("../escape")


@pytest.mark.asyncio
async def test_invalid_key_rejected_on_delete() -> None:
    store = InMemoryStore(_ns())
    with pytest.raises(NamespaceViolation):
        await store.delete("with space")


@pytest.mark.asyncio
async def test_two_stores_isolated() -> None:
    """Stores with different namespaces share no state."""
    a = InMemoryStore(_ns(name="ns-a"))
    b = InMemoryStore(_ns(name="ns-b"))
    await a.write("k", b"from-a")
    await b.write("k", b"from-b")
    assert await a.read("k") == b"from-a"
    assert await b.read("k") == b"from-b"


@pytest.mark.asyncio
async def test_concurrent_writes_last_wins() -> None:
    """Concurrent writes to the same key produce a stable final value."""
    store = InMemoryStore(_ns())

    async def writer(value: bytes) -> None:
        for _ in range(50):
            await store.write("k", value)

    await asyncio.gather(writer(b"a"), writer(b"b"), writer(b"c"))
    final = await store.read("k")
    assert final in (b"a", b"b", b"c")


@pytest.mark.asyncio
async def test_in_memory_store_satisfies_protocol() -> None:
    store = InMemoryStore(_ns())
    assert isinstance(store, MemoryStore)


@pytest.mark.asyncio
async def test_namespace_exposed_via_property() -> None:
    ns = _ns(name="visible")
    store = InMemoryStore(ns)
    assert store.namespace is ns


@pytest.mark.asyncio
async def test_bytes_only_on_the_wire() -> None:
    """The store accepts and returns bytes, not str or other types."""
    store = InMemoryStore(_ns())
    payload = b"\x00\x01\x02\x03binary\xff"
    await store.write("k", payload)
    result = await store.read("k")
    assert isinstance(result, bytes)
    assert result == payload
