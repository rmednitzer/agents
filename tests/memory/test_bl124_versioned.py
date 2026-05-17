"""Tests for BL-124: VersionedMemoryStore MVCC version tokens."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from memory.inmemory import InMemoryStore
from memory.sqlite import SQLiteStore
from memory.store import VersionedMemoryStore
from memory.types import Namespace


def _ns(retention: float | None = None) -> Namespace:
    return Namespace(name="ns", workload="w", retention_seconds=retention)


@pytest.fixture(params=["inmemory", "sqlite"])
def store(request: pytest.FixtureRequest) -> Iterator[VersionedMemoryStore]:
    if request.param == "inmemory":
        yield InMemoryStore(_ns())
    else:
        s = SQLiteStore(_ns())
        yield s
        s.close()


def test_satisfies_protocol(store: VersionedMemoryStore) -> None:
    assert isinstance(store, VersionedMemoryStore)


@pytest.mark.asyncio
async def test_read_versioned_absent_is_none(store: VersionedMemoryStore) -> None:
    assert await store.read_versioned("missing") is None


@pytest.mark.asyncio
async def test_write_versioned_requires_absent_when_expected_none(
    store: VersionedMemoryStore,
) -> None:
    tok = await store.write_versioned("k", b"v1", expected_version=None)
    assert tok is not None
    rv = await store.read_versioned("k")
    assert rv is not None
    value, token = rv
    assert value == b"v1"
    assert token == tok
    # A second create with expected_version=None must conflict (present).
    assert await store.write_versioned("k", b"v2", expected_version=None) is None
    again = await store.read_versioned("k")
    assert again is not None
    assert again[0] == b"v1"


@pytest.mark.asyncio
async def test_optimistic_update_succeeds_then_stale_fails(
    store: VersionedMemoryStore,
) -> None:
    t1 = await store.write_versioned("k", b"v1")
    assert t1 is not None
    t2 = await store.write_versioned("k", b"v2", expected_version=t1)
    assert t2 is not None
    assert t2 != t1
    # Re-using the stale token is a conflict; the value is unchanged.
    assert await store.write_versioned("k", b"v3", expected_version=t1) is None
    rv = await store.read_versioned("k")
    assert rv is not None
    assert rv[0] == b"v2"


@pytest.mark.asyncio
async def test_token_is_path_independent(store: VersionedMemoryStore) -> None:
    """A plain write changes the value, so the MVCC token must change
    and a versioned write expecting the old token must conflict."""
    t1 = await store.write_versioned("k", b"v1")
    assert t1 is not None
    await store.write("k", b"changed-out-of-band")
    assert await store.write_versioned("k", b"next", expected_version=t1) is None
    rv = await store.read_versioned("k")
    assert rv is not None
    assert rv[0] == b"changed-out-of-band"


@pytest.mark.asyncio
async def test_delete_versioned_matches_token(store: VersionedMemoryStore) -> None:
    t1 = await store.write_versioned("k", b"v1")
    assert t1 is not None
    assert await store.delete_versioned("k", "wrong-token") is False
    assert await store.read("k") == b"v1"
    assert await store.delete_versioned("k", t1) is True
    assert await store.read("k") is None
    assert await store.delete_versioned("k", t1) is False


@pytest.mark.asyncio
async def test_wrap_acl_forwards_versioned_protocol() -> None:
    """wrap_acl over a versioned backend keeps a truthful isinstance and
    gates the versioned methods (Codex P2; BL-156 contract)."""
    from memory.acl import RoleACL, wrap_acl
    from memory.errors import AccessDenied

    inner = InMemoryStore(_ns())
    policy = RoleACL(
        roles={"admin": "rw", "bob": "ro"},
        grants={"rw": {"read", "write", "delete"}, "ro": {"read"}},
    )
    admin = wrap_acl(inner, policy, "admin")
    assert isinstance(admin, VersionedMemoryStore)
    tok = await admin.write_versioned("k", b"v1")
    assert tok is not None
    rv = await admin.read_versioned("k")
    assert rv is not None
    assert rv[0] == b"v1"
    assert await admin.delete_versioned("k", rv[1]) is True

    await admin.write_versioned("k", b"v2")
    reader = wrap_acl(inner, policy, "bob")
    assert isinstance(reader, VersionedMemoryStore)
    got = await reader.read_versioned("k")
    assert got is not None
    with pytest.raises(AccessDenied):
        await reader.write_versioned("k", b"nope", expected_version=got[1])


@pytest.mark.asyncio
async def test_identical_content_is_no_conflict_aba(
    store: VersionedMemoryStore,
) -> None:
    t1 = await store.write_versioned("k", b"same")
    assert t1 is not None
    # Re-writing identical bytes yields the same content token, so a
    # writer holding t1 can still commit (documented content-version).
    t2 = await store.write_versioned("k", b"same", expected_version=t1)
    assert t2 == t1
