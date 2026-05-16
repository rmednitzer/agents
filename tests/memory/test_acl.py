"""Tests for memory.acl.ACLStore + RoleACL (BL-071)."""

from __future__ import annotations

import pytest

from memory.acl import ACLStore, RoleACL
from memory.encryption import EncryptedStore, StaticKeyProvider
from memory.errors import AccessDenied
from memory.inmemory import InMemoryStore
from memory.store import MemoryStore
from memory.types import Namespace


def _inner() -> InMemoryStore:
    return InMemoryStore(Namespace(name="ns", workload="w"))


def _policy() -> RoleACL:
    return RoleACL(
        roles={"alice": "admin", "bob": "reader", "carol": "scoped"},
        grants={
            "admin": {"read", "write", "delete", "list"},
            "reader": {"read", "list"},
            "scoped": {"read", "write"},
        },
        prefixes={"scoped": ["team-a."]},
    )


def test_satisfies_protocol() -> None:
    assert isinstance(ACLStore(_inner(), _policy(), "alice"), MemoryStore)


@pytest.mark.asyncio
async def test_admin_full_access() -> None:
    s = ACLStore(_inner(), _policy(), "alice")
    await s.write("k", b"v")
    assert await s.read("k") == b"v"
    assert await s.list_keys() == ["k"]
    await s.delete("k")


@pytest.mark.asyncio
async def test_reader_cannot_write_or_delete() -> None:
    inner = _inner()
    await inner.write("k", b"v")
    s = ACLStore(inner, _policy(), "bob")
    assert await s.read("k") == b"v"
    with pytest.raises(AccessDenied, match="write"):
        await s.write("k", b"x")
    with pytest.raises(AccessDenied, match="delete"):
        await s.delete("k")


@pytest.mark.asyncio
async def test_unknown_principal_denied_everything() -> None:
    s = ACLStore(_inner(), _policy(), "mallory")
    with pytest.raises(AccessDenied):
        await s.read("k")


@pytest.mark.asyncio
async def test_prefix_scoped_role() -> None:
    inner = _inner()
    s = ACLStore(inner, _policy(), "carol")
    await s.write("team-a.doc", b"v")  # in scope
    assert await s.read("team-a.doc") == b"v"
    with pytest.raises(AccessDenied):
        await s.write("team-b.doc", b"v")  # out of scope


@pytest.mark.asyncio
async def test_acl_composes_over_encryption() -> None:
    inner = _inner()
    enc = EncryptedStore(inner, StaticKeyProvider(b"0" * 32))
    s = ACLStore(enc, _policy(), "alice")
    await s.write("k", b"secret")
    assert await s.read("k") == b"secret"
    raw = await inner.read("k")
    assert raw is not None
    assert b"secret" not in raw  # still encrypted at rest
