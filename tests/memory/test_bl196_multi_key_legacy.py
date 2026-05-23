"""Tests for BL-196: multi-key legacy fallback on `EncryptedStore`
(runbook 7.4 candidate 4).

Lifts the L16 "current-key only" migration restriction (`BL-181`) when
the operator opts in: an `EncryptedStore` constructed with
``legacy_multi_key=True`` over an `IterableKeyProvider` decrypts a
pre-rotation legacy value sealed under *any* historical key in the
ring, not only the current one. AES-GCM authentication still
guarantees no silent wrong plaintext.
"""

from __future__ import annotations

import pytest

from memory.encryption import (
    EncryptedStore,
    IterableKeyProvider,
    RotatingKeyProvider,
    StaticKeyProvider,
    wrap_encrypted,
)
from memory.inmemory import InMemoryStore
from memory.types import Namespace

pytest.importorskip("cryptography")

_K1 = b"1" * 32
_K2 = b"2" * 32
_K3 = b"3" * 32


def _inner(ns: str = "ns") -> InMemoryStore:
    return InMemoryStore(Namespace(name=ns, workload="w"))


# --- IterableKeyProvider on RotatingKeyProvider -----------------------


def test_rotating_key_provider_iter_key_ids_returns_insertion_order() -> None:
    """``iter_key_ids`` yields the seed key first, then each ``rotate``
    in chronological order. EncryptedStore's multi-key fallback skips
    the current key and iterates the rest."""
    kp = RotatingKeyProvider({"v1": _K1}, "v1")
    kp.rotate("v2", _K2)
    kp.rotate("v3", _K3)
    assert list(kp.iter_key_ids("ns")) == ["v1", "v2", "v3"]


def test_rotating_key_provider_satisfies_iterable_protocol() -> None:
    """The Protocol is ``runtime_checkable``; in-tree provider matches."""
    kp = RotatingKeyProvider({"v1": _K1}, "v1")
    assert isinstance(kp, IterableKeyProvider)


# --- Construction validation ------------------------------------------


def test_legacy_multi_key_requires_versioned_provider() -> None:
    """A plain KeyProvider has no key ring; the flag must be rejected at
    construction (additive-to-L1: surface configuration errors at load
    time, not mid-run)."""
    with pytest.raises(ValueError, match="VersionedKeyProvider"):
        EncryptedStore(_inner(), StaticKeyProvider(_K1), legacy_multi_key=True)


def test_legacy_multi_key_requires_iterable_provider() -> None:
    """A VersionedKeyProvider that does not expose ``iter_key_ids``
    cannot participate in the multi-key fallback. Rejected at
    construction."""

    class _NotIterable:
        # Implements VersionedKeyProvider (current_key + key) but NOT
        # IterableKeyProvider.
        def current_key(self, namespace: str) -> tuple[str, bytes]:
            return "v1", _K1

        def key(self, namespace: str, key_id: str) -> bytes:
            return _K1

    with pytest.raises(ValueError, match="IterableKeyProvider"):
        EncryptedStore(_inner(), _NotIterable(), legacy_multi_key=True)


# --- Default (off): preserves BL-181 current-key-only behaviour --------


@pytest.mark.asyncio
async def test_default_is_current_key_only_preserves_bl181() -> None:
    """Without ``legacy_multi_key=True`` the BL-181 behaviour is
    unchanged: a legacy value sealed under a non-current key still
    fails (the L16 restriction)."""
    backend = _inner()
    # Seal with K1 via a plain provider (legacy on-disk format).
    await EncryptedStore(backend, StaticKeyProvider(_K1)).write("k", b"secret")
    # Adopt a versioned provider whose current key is K2; K1 is in the
    # ring but K2 is current. Default flag => current-key-only fallback
    # tries K2, which fails; the legacy value is not reachable.
    versioned = EncryptedStore(backend, RotatingKeyProvider({"v1": _K1, "v2": _K2}, "v2"))
    with pytest.raises((ValueError, KeyError)) as exc:
        await versioned.read("k")
    assert b"secret" not in str(exc.value).encode()


# --- Multi-key fallback (on): reads legacy data under any ring key ----


@pytest.mark.asyncio
async def test_multi_key_reads_legacy_under_historical_key() -> None:
    """With ``legacy_multi_key=True`` the fallback iterates the ring:
    a legacy value sealed under a non-current key decrypts."""
    backend = _inner()
    await EncryptedStore(backend, StaticKeyProvider(_K1)).write("k", b"secret")
    # Current key is v2 (a rotated-away successor); v1 is in the ring.
    versioned = EncryptedStore(
        backend,
        RotatingKeyProvider({"v1": _K1, "v2": _K2}, "v2"),
        legacy_multi_key=True,
    )
    assert await versioned.read("k") == b"secret"


@pytest.mark.asyncio
async def test_multi_key_reads_legacy_under_oldest_key_in_chain() -> None:
    """The iteration covers the full chain: a legacy value sealed under
    the oldest historical key still decrypts after multiple rotations."""
    backend = _inner()
    await EncryptedStore(backend, StaticKeyProvider(_K1)).write("k", b"old-secret")
    # Ring: v1 (seed), v2, v3 (current). Legacy data is K1.
    kp = RotatingKeyProvider({"v1": _K1}, "v1")
    kp.rotate("v2", _K2)
    kp.rotate("v3", _K3)
    versioned = EncryptedStore(backend, kp, legacy_multi_key=True)
    assert await versioned.read("k") == b"old-secret"


@pytest.mark.asyncio
async def test_multi_key_still_authenticated_no_silent_wrong_value() -> None:
    """Even with the multi-key fallback, AES-GCM auth still gates each
    attempt: a legacy value sealed under a key NOT in the ring fails
    every historical attempt, and the original envelope error is
    surfaced (never a wrong plaintext)."""
    backend = _inner()
    # Seal with K1.
    await EncryptedStore(backend, StaticKeyProvider(_K1)).write("k", b"secret")
    # Ring contains K2 and K3 only; no key in the ring matches the
    # legacy ciphertext. All N attempts fail.
    versioned = EncryptedStore(
        backend,
        RotatingKeyProvider({"v2": _K2, "v3": _K3}, "v3"),
        legacy_multi_key=True,
    )
    with pytest.raises((ValueError, KeyError)) as exc:
        await versioned.read("k")
    assert b"secret" not in str(exc.value).encode()


@pytest.mark.asyncio
async def test_multi_key_preferred_after_envelope_for_versioned_envelope() -> None:
    """A genuine versioned envelope still decrypts via the envelope path;
    the multi-key fallback does not run for envelope hits."""
    backend = _inner()
    store = EncryptedStore(backend, RotatingKeyProvider({"v1": _K1}, "v1"), legacy_multi_key=True)
    await store.write("k", b"enveloped")
    raw = await backend.read("k")
    assert raw is not None
    assert raw[1 : 1 + raw[0]] == b"v1"  # envelope on disk
    assert await store.read("k") == b"enveloped"


@pytest.mark.asyncio
async def test_multi_key_rejects_truly_malformed_value() -> None:
    """A value too short for even a nonce is not retried (no chance the
    legacy fallback could ever match). The original envelope error is
    raised, matching the BL-181 fast-path."""
    backend = _inner()
    # Write a too-short raw value directly under the InMemoryStore.
    await backend.write("k", b"\x00")
    versioned = EncryptedStore(
        backend, RotatingKeyProvider({"v1": _K1}, "v1"), legacy_multi_key=True
    )
    with pytest.raises(ValueError, match="envelope"):
        await versioned.read("k")


# --- wrap_encrypted forwards the flag ---------------------------------


@pytest.mark.asyncio
async def test_wrap_encrypted_forwards_legacy_multi_key_flag() -> None:
    """``wrap_encrypted`` accepts the flag and produces a store with
    multi-key fallback active over the inner adapter's extension
    Protocols. Regression guard against forgetting to thread the kwarg
    through the factory."""
    backend = _inner()  # InMemoryStore supports Batch/Scan/Sweepable
    await EncryptedStore(backend, StaticKeyProvider(_K1)).write("k", b"secret")
    store = wrap_encrypted(
        backend,
        RotatingKeyProvider({"v1": _K1, "v2": _K2}, "v2"),
        legacy_multi_key=True,
    )
    assert await store.read("k") == b"secret"
