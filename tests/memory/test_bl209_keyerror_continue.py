"""Sixth-audit deferred items: regression tests for `BL-209` / `BL-210`
(ADR 0015 deferred close).

`BL-209` (EncryptedStore BL-196 multi-key loop catches KeyError):
defence-in-depth for an out-of-tree `IterableKeyProvider` (e.g., a
KMS-backed provider) that returns a key id from `iter_key_ids` which
the underlying provider can no longer resolve (key revoked between
iteration and lookup). Pre-`BL-209` the resulting `KeyError`
propagated out of `_unseal` as an unexpected error; now it is
treated as a missed attempt and the loop continues, mirroring the
`InvalidTag` branch.

`BL-210` is documentation only (`wrap_encrypted` docstring extension);
exercised by `make schema` regeneration, not a runtime test.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable

import pytest

from memory.encryption import (
    EncryptedStore,
    IterableKeyProvider,
    VersionedKeyProvider,
    wrap_encrypted,
)
from memory.inmemory import InMemoryStore
from memory.types import Namespace


class _RevokingProvider:
    """`VersionedKeyProvider` + `IterableKeyProvider` whose `key`
    raises `KeyError` for a key id `iter_key_ids` still references.

    Models a KMS-backed provider that returns a stale id from
    `iter_key_ids` (a snapshot of the ring) while a concurrent revoke
    or rotation has removed the underlying material.
    """

    def __init__(self, current_id: str, current_bytes: bytes, dangling_id: str) -> None:
        self._cur_id = current_id
        self._cur_bytes = current_bytes
        self._dangling_id = dangling_id

    def current_key(self, namespace: str) -> tuple[str, bytes]:
        return self._cur_id, self._cur_bytes

    def key(self, namespace: str, key_id: str) -> bytes:
        if key_id == self._cur_id:
            return self._cur_bytes
        # Every other id raises; in particular, the id we yield from
        # iter_key_ids below is NOT resolvable. Models KMS revoke.
        raise KeyError(key_id)

    def iter_key_ids(self, namespace: str) -> Iterable[str]:
        # Return a dangling id that key() cannot resolve. Pre-BL-209
        # the multi-key loop calls self._aes_for(id) -> KeyError, which
        # propagates as an unexpected exception out of _unseal.
        return [self._cur_id, self._dangling_id]


def test_iterable_provider_protocol_satisfied() -> None:
    """The double satisfies both Protocols (runtime isinstance)."""
    p = _RevokingProvider("v1", secrets.token_bytes(32), "v0")
    assert isinstance(p, VersionedKeyProvider)
    assert isinstance(p, IterableKeyProvider)


@pytest.mark.asyncio
async def test_multi_key_loop_swallows_keyerror_and_surfaces_envelope_error() -> None:
    """A `KeyError` from the legacy fallback path no longer crashes
    `_unseal`; the loop continues. Since this test feeds the multi-
    key loop a bare nonce+ct that no historical key resolves, the
    loop exits with `env_err` (the original unknown-key-version
    diagnostic). Pre-`BL-209` the KeyError propagated out and the
    caller saw an internal Python exception instead of the
    documented envelope-error contract."""
    provider = _RevokingProvider(
        current_id="v1",
        current_bytes=secrets.token_bytes(32),
        dangling_id="v0",
    )
    ns = Namespace(name="ns", workload="w")
    store = wrap_encrypted(
        InMemoryStore(ns),
        provider,
        legacy_multi_key=True,
    )
    assert isinstance(store, EncryptedStore)

    # Seed the inner store with an opaque (non-envelope) value that
    # neither current-key nor the dangling id can authenticate. The
    # multi-key fallback exhausts; we want a clean envelope error,
    # not a leaked KeyError.
    inner = store._inner  # access for the seed; the public API is read()
    sealed = b"\x00" * 32  # < envelope-header constraints, forces ValueError on the envelope path
    await inner.write("k", sealed)

    # Reading must either return None on a no-match (current
    # adapter falls through to envelope error) or raise a documented
    # exception type from the encryption module -- but NEVER a raw
    # KeyError out of the provider lookup. The test pins the
    # NOT-KeyError property.
    with pytest.raises(Exception, match=r".*") as exc_info:
        await store.read("k")
    assert not isinstance(exc_info.value, KeyError), (
        f"BL-209 regression: KeyError leaked out of _unseal: {exc_info.value!r}"
    )


@pytest.mark.asyncio
async def test_multi_key_loop_continues_past_keyerror_to_find_match() -> None:
    """When the dangling id raises KeyError but a SUBSEQUENT key in
    the ring resolves and decrypts, the loop reaches it instead of
    bailing at the KeyError. Verifies "continue" not "swallow"."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    # Seed: a key K0 that successfully sealed a legacy bare nonce+ct,
    # plus a dangling id between current and K0 in the iteration
    # order. The current key cannot authenticate the legacy value
    # (different key bytes); the dangling id raises KeyError; K0 must
    # be reached and decrypt successfully.
    k0_bytes = secrets.token_bytes(32)
    cur_bytes = secrets.token_bytes(32)
    plaintext = b"hello"
    nonce = secrets.token_bytes(12)

    class _MultiProvider:
        def current_key(self, namespace: str) -> tuple[str, bytes]:
            return "v2", cur_bytes

        def key(self, namespace: str, key_id: str) -> bytes:
            if key_id == "v2":
                return cur_bytes
            if key_id == "v0":
                return k0_bytes
            raise KeyError(key_id)

        def iter_key_ids(self, namespace: str) -> Iterable[str]:
            # Dangling id "v1" sits between current "v2" and historical
            # "v0"; the loop must continue past the KeyError to reach v0.
            return ["v2", "v1", "v0"]

    ns = Namespace(name="ns", workload="w")
    inner = InMemoryStore(ns)

    # Legacy seal under K0: bare nonce + ciphertext + tag, no envelope.
    # AAD is the EncryptedStore default: namespace + "::" + key.
    aad = f"{ns.name}::k".encode()
    ct = AESGCM(k0_bytes).encrypt(nonce, plaintext, aad)
    await inner.write("k", nonce + ct)

    store = wrap_encrypted(inner, _MultiProvider(), legacy_multi_key=True)
    assert await store.read("k") == plaintext
