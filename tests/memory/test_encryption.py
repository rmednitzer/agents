"""Tests for memory.encryption.EncryptedStore (BL-070)."""

from __future__ import annotations

import pytest

from memory.encryption import EncryptedStore, StaticKeyProvider
from memory.inmemory import InMemoryStore
from memory.store import MemoryStore
from memory.types import Namespace

pytest.importorskip("cryptography")

_KEY = b"0" * 32


def _enc(ns: str = "ns") -> tuple[EncryptedStore, InMemoryStore]:
    inner = InMemoryStore(Namespace(name=ns, workload="w"))
    return EncryptedStore(inner, StaticKeyProvider(_KEY)), inner


def test_satisfies_memory_store_protocol() -> None:
    enc, _ = _enc()
    assert isinstance(enc, MemoryStore)


def test_static_key_provider_validates_length() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        StaticKeyProvider(b"short")


@pytest.mark.asyncio
async def test_roundtrip_plaintext_for_caller() -> None:
    enc, _ = _enc()
    await enc.write("k", b"secret payload")
    assert await enc.read("k") == b"secret payload"
    assert await enc.read("missing") is None


@pytest.mark.asyncio
async def test_backend_only_sees_ciphertext() -> None:
    enc, inner = _enc()
    await enc.write("k", b"secret payload")
    stored = await inner.read("k")
    assert stored is not None
    assert b"secret payload" not in stored
    assert len(stored) > len(b"secret payload")  # nonce + tag overhead


@pytest.mark.asyncio
async def test_ciphertext_not_replayable_across_keys() -> None:
    from cryptography.exceptions import InvalidTag

    enc, inner = _enc()
    await enc.write("k1", b"v")
    sealed = await inner.read("k1")
    assert sealed is not None
    # Move k1's ciphertext under k2; AAD binds key, so decrypt must fail.
    await inner.write("k2", sealed)
    with pytest.raises(InvalidTag):
        await enc.read("k2")


@pytest.mark.asyncio
async def test_delete_and_list_pass_through() -> None:
    enc, _ = _enc()
    await enc.write("a", b"1")
    await enc.write("b", b"2")
    assert await enc.list_keys() == ["a", "b"]
    await enc.delete("a")
    assert await enc.list_keys() == ["b"]


@pytest.mark.asyncio
async def test_namespace_from_inner() -> None:
    enc, inner = _enc("specific")
    assert enc.namespace is inner.namespace
    assert enc.namespace.name == "specific"
