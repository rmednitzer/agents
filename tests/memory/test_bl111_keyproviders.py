"""Tests for BL-111: env/file/rotating key providers + rotation envelope."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from memory.encryption import (
    EncryptedStore,
    EnvKeyProvider,
    FileKeyProvider,
    KeyProvider,
    RotatingKeyProvider,
    StaticKeyProvider,
    VersionedKeyProvider,
    wrap_encrypted,
)
from memory.inmemory import InMemoryStore
from memory.store import BatchMemoryStore
from memory.types import Namespace

pytest.importorskip("cryptography")

_K1 = b"1" * 32
_K2 = b"2" * 32


def _inner(ns: str = "ns") -> InMemoryStore:
    return InMemoryStore(Namespace(name=ns, workload="w"))


# --- EnvKeyProvider ---------------------------------------------------


def test_env_key_provider_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTS_MEMORY_KEY", base64.b64encode(_K1).decode())
    p = EnvKeyProvider()
    assert isinstance(p, KeyProvider)
    assert p.key_for("ns") == _K1


def test_env_key_provider_hex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYKEY", _K1.hex())
    assert EnvKeyProvider("MYKEY", encoding="hex").key_for("ns") == _K1


def test_env_key_provider_missing_is_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTS_MEMORY_KEY", raising=False)
    with pytest.raises(ValueError, match="is not set"):
        EnvKeyProvider().key_for("ns")


def test_env_key_provider_rejects_wrong_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_MEMORY_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(ValueError, match="32 bytes"):
        EnvKeyProvider().key_for("ns")


# --- FileKeyProvider --------------------------------------------------


def test_file_key_provider_raw(tmp_path: Path) -> None:
    f = tmp_path / "key.bin"
    f.write_bytes(_K1)
    p = FileKeyProvider(f)
    assert isinstance(p, KeyProvider)
    assert p.key_for("ns") == _K1


def test_file_key_provider_base64(tmp_path: Path) -> None:
    f = tmp_path / "key.b64"
    f.write_text(base64.b64encode(_K2).decode())
    assert FileKeyProvider(f, encoding="base64").key_for("ns") == _K2


def test_file_key_provider_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        FileKeyProvider(tmp_path / "absent").key_for("ns")


# --- RotatingKeyProvider + rotation envelope --------------------------


def test_rotating_provider_is_versioned() -> None:
    p = RotatingKeyProvider({"v1": _K1}, "v1")
    assert isinstance(p, VersionedKeyProvider)
    assert p.current_key("ns") == ("v1", _K1)
    assert p.key("ns", "v1") == _K1


def test_rotating_provider_rejects_unknown_version() -> None:
    p = RotatingKeyProvider({"v1": _K1}, "v1")
    with pytest.raises(KeyError, match="unknown key version"):
        p.key("ns", "v9")


def test_rotating_provider_validates_current() -> None:
    with pytest.raises(ValueError, match="not in the key ring"):
        RotatingKeyProvider({"v1": _K1}, "missing")


@pytest.mark.asyncio
async def test_value_written_before_rotation_still_decrypts() -> None:
    """The core BL-111 guarantee: rotating the current key does not
    strand ciphertext sealed under a prior version."""
    kp = RotatingKeyProvider({"v1": _K1}, "v1")
    store = EncryptedStore(_inner(), kp)
    await store.write("k", b"old-secret")
    # Rotate: new writes use v2, old value stays readable under v1.
    kp.rotate("v2", _K2)
    assert await store.read("k") == b"old-secret"
    await store.write("k2", b"new-secret")
    assert await store.read("k2") == b"new-secret"


@pytest.mark.asyncio
async def test_versioned_envelope_carries_key_id_and_encrypts() -> None:
    kp = RotatingKeyProvider({"v1": _K1}, "v1")
    store = EncryptedStore(_inner(), kp)
    inner = store._inner  # type: ignore[attr-defined]
    plaintext = b"plaintext-payload"
    await store.write("a", plaintext)
    sealed = await inner.read("a")
    assert sealed is not None
    # Envelope: [len][key-id][nonce][ciphertext+tag]. The id is "v1".
    assert sealed[1 : 1 + sealed[0]] == b"v1"
    # The backend never sees the plaintext, and the value is expanded
    # by the envelope + nonce + GCM tag.
    assert plaintext not in sealed
    assert len(sealed) > len(plaintext)
    assert await store.read("a") == plaintext


@pytest.mark.asyncio
async def test_static_provider_format_is_unchanged() -> None:
    """A plain KeyProvider keeps the exact prior on-disk format (no
    envelope): additive, so existing encrypted data is unaffected."""
    store = EncryptedStore(_inner(), StaticKeyProvider(_K1))
    inner = store._inner  # type: ignore[attr-defined]
    await store.write("k", b"data")
    sealed = await inner.read("k")
    assert sealed is not None
    # Legacy layout is exactly nonce(12) + ciphertext+tag; AES-GCM tag
    # is 16 bytes, so total == 12 + len(pt) + 16.
    assert len(sealed) == 12 + len(b"data") + 16
    assert await store.read("k") == b"data"


@pytest.mark.asyncio
async def test_wrap_encrypted_accepts_versioned_provider() -> None:
    kp = RotatingKeyProvider({"v1": _K1}, "v1")
    store = wrap_encrypted(_inner(), kp)
    assert isinstance(store, BatchMemoryStore)
    await store.mset({"a": b"1", "b": b"2"})
    kp.rotate("v2", _K2)
    assert await store.mget(["a", "b"]) == [b"1", b"2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupt",
    [b"", b"\x00rest", b"\x02v1short"],
    ids=["empty", "zero-len-id", "truncated-body"],
)
async def test_malformed_versioned_envelope_raises_value_error(corrupt: bytes) -> None:
    """A truncated/corrupt stored value (backend trust boundary) raises
    a controlled ValueError, not IndexError/UnicodeDecodeError."""
    kp = RotatingKeyProvider({"v1": _K1}, "v1")
    store = EncryptedStore(_inner(), kp)
    inner = store._inner  # type: ignore[attr-defined]
    await inner.write("k", corrupt)
    with pytest.raises(ValueError, match="malformed encrypted envelope"):
        await store.read("k")


@pytest.mark.asyncio
async def test_seal_does_not_double_lookup_current_key() -> None:
    """_seal uses current_key()'s returned bytes; it must not also call
    provider.key() for the same id (a KMS provider would double the
    lookup per write)."""

    class _Counting:
        def __init__(self) -> None:
            self.current_calls = 0
            self.key_calls = 0

        def current_key(self, namespace: str) -> tuple[str, bytes]:
            self.current_calls += 1
            return "v1", _K1

        def key(self, namespace: str, key_id: str) -> bytes:
            self.key_calls += 1
            return _K1

    kp = _Counting()
    assert isinstance(kp, VersionedKeyProvider)
    store = EncryptedStore(_inner(), kp)
    await store.write("k", b"payload")
    assert kp.current_calls == 1
    assert kp.key_calls == 0
    # A read of a current-key value hits the _seal-populated cache too.
    assert await store.read("k") == b"payload"
    assert kp.key_calls == 0
