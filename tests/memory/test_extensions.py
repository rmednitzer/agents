"""Memory extension Protocols: batch, scan, content-addressing, CAS.

Covers BL-072, BL-081, BL-082, BL-083.
"""

from __future__ import annotations

import hashlib

import pytest

from memory.errors import NamespaceViolation
from memory.inmemory import InMemoryStore
from memory.store import (
    BatchMemoryStore,
    CASMemoryStore,
    ContentAddressableStore,
    ScannableStore,
)
from memory.types import Namespace


def _store(retention: float | None = None) -> InMemoryStore:
    return InMemoryStore(Namespace(name="ext", workload="w", retention_seconds=retention))


@pytest.mark.asyncio
async def test_inmemory_satisfies_extension_protocols() -> None:
    s = _store()
    assert isinstance(s, BatchMemoryStore)
    assert isinstance(s, ScannableStore)
    assert isinstance(s, ContentAddressableStore)
    assert isinstance(s, CASMemoryStore)


# --- BL-081 batch --------------------------------------------------------


@pytest.mark.asyncio
async def test_mset_mget_preserve_order_and_misses() -> None:
    s = _store()
    await s.mset({"a": b"1", "b": b"2"})
    assert await s.mget(["a", "missing", "b"]) == [b"1", None, b"2"]


@pytest.mark.asyncio
async def test_mdelete_idempotent() -> None:
    s = _store()
    await s.mset({"a": b"1", "b": b"2"})
    await s.mdelete(["a", "never"])
    assert await s.list_keys() == ["b"]


@pytest.mark.asyncio
async def test_batch_validates_all_keys_before_mutating() -> None:
    s = _store()
    with pytest.raises(NamespaceViolation):
        await s.mset({"ok": b"1", "bad key": b"2"})
    assert await s.list_keys() == []  # all-or-nothing on validation


# --- BL-082 scan ---------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_pages_through_all_keys_without_overlap() -> None:
    s = _store()
    await s.mset({f"k{i:02d}": b"v" for i in range(10)})
    seen: list[str] = []
    cursor = ""
    pages = 0
    while True:
        cursor, page = await s.scan(cursor=cursor, count=3)
        seen.extend(page)
        pages += 1
        if cursor == "":
            break
    assert seen == sorted(f"k{i:02d}" for i in range(10))
    assert len(seen) == len(set(seen))
    assert pages == 4  # 3 + 3 + 3 + 1


@pytest.mark.asyncio
async def test_scan_respects_prefix_and_zero_count() -> None:
    s = _store()
    await s.mset({"a-1": b"v", "a-2": b"v", "b-1": b"v"})
    cursor, page = await s.scan(prefix="a-", count=100)
    assert page == ["a-1", "a-2"]
    assert cursor == ""
    assert await s.scan(count=0) == ("", [])


# --- BL-083 content addressing ------------------------------------------


@pytest.mark.asyncio
async def test_write_content_returns_sha256_and_is_idempotent() -> None:
    s = _store()
    key = await s.write_content(b"payload")
    assert key == hashlib.sha256(b"payload").hexdigest()
    assert await s.read(key) == b"payload"
    assert await s.write_content(b"payload") == key  # idempotent


# --- BL-072 CAS ----------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_and_set_absent_then_conflict() -> None:
    s = _store()
    assert await s.compare_and_set("k", None, b"v1") is True  # was absent
    assert await s.compare_and_set("k", None, b"v2") is False  # now present
    assert await s.compare_and_set("k", b"v1", b"v2") is True  # matches
    assert await s.read("k") == b"v2"


@pytest.mark.asyncio
async def test_compare_and_delete_only_on_match() -> None:
    s = _store()
    await s.write("k", b"v")
    assert await s.compare_and_delete("k", b"wrong") is False
    assert await s.compare_and_delete("k", b"v") is True
    assert await s.read("k") is None
