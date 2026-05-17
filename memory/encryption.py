"""Encryption at rest for memory adapters (BL-070, ADR 0007).

ADR 0004 deferred encryption as "a per-adapter concern; the framework
should provide a KeyProvider Protocol". EncryptedStore is a transparent
MemoryStore decorator: it wraps any MemoryStore and encrypts values
before they reach the backend, decrypts on read. The backend only ever
sees ciphertext.

- Values are sealed with AES-256-GCM (authenticated). ``cryptography``
  is an optional dependency, imported lazily (``pip install
  'agents[crypto]'``).
- The namespace name and key are bound as additional authenticated data
  (AAD), so ciphertext cannot be replayed across namespaces/keys.
- Keys (the dictionary kind) are NOT encrypted -- only values. The
  backend still needs plaintext keys to index; that is the documented
  boundary. Use opaque keys if key confidentiality is required.
- A fresh 96-bit nonce per write is prepended to the ciphertext.

KeyProvider abstracts key sourcing (static, KMS, env, rotation). The
provider returns raw 32-byte keys; rotation/versioning is the
provider's concern.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, runtime_checkable

from memory.store import (
    BatchMemoryStore,
    ContentAddressableStore,
    MemoryStore,
    ScannableStore,
    SweepableStore,
)
from memory.types import Namespace
from memory.validators import validate_key

__all__ = ["EncryptedStore", "KeyProvider", "StaticKeyProvider", "wrap_encrypted"]

_NONCE_BYTES = 12


@runtime_checkable
class KeyProvider(Protocol):
    """Sources the symmetric key for a namespace.

    Implementations may return a process-static key, fetch from a KMS,
    or rotate; the contract is only that ``key_for`` returns a 32-byte
    (AES-256) key for the namespace.
    """

    def key_for(self, namespace: str) -> bytes: ...


class StaticKeyProvider:
    """A fixed 32-byte key for every namespace (dev/tests/single-tenant)."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("AES-256 key must be exactly 32 bytes")
        self._key = key

    def key_for(self, namespace: str) -> bytes:
        return self._key


class EncryptedStore:
    """Wraps a MemoryStore, sealing values with AES-256-GCM.

    The wrapped store is the source of truth for the namespace, key
    validation, TTL, and audit; this decorator only transforms the
    value bytes. It satisfies the MemoryStore Protocol so it composes
    anywhere a store is expected (including under ACLStore).
    """

    name: str = "encrypted"

    def __init__(self, inner: MemoryStore, key_provider: KeyProvider) -> None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ImportError(
                "EncryptedStore requires the 'crypto' extra: pip install 'agents[crypto]'"
            ) from exc
        self._inner = inner
        self._aes = AESGCM(key_provider.key_for(inner.namespace.name))

    @property
    def namespace(self) -> Namespace:
        return self._inner.namespace

    def _aad(self, key: str) -> bytes:
        return f"{self._inner.namespace.name}::{key}".encode()

    async def read(self, key: str) -> bytes | None:
        # Validate before deriving AAD: the MemoryStore Protocol requires
        # key validation before any keyed operation, and a key carrying
        # the '::' separator would otherwise let AAD collide across keys
        # (the inner store also validates, but the AAD is built here).
        validate_key(key)
        return self._unseal(key, await self._inner.read(key))

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        validate_key(key)
        await self._inner.write(key, self._seal(key, value), ttl_seconds=ttl_seconds)

    async def delete(self, key: str) -> None:
        validate_key(key)
        await self._inner.delete(key)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return await self._inner.list_keys(prefix)

    def _seal(self, key: str, value: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return nonce + self._aes.encrypt(nonce, value, self._aad(key))

    def _unseal(self, key: str, sealed: bytes | None) -> bytes | None:
        if sealed is None:
            return None
        nonce, ct = sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:]
        return bytes(self._aes.decrypt(nonce, ct, self._aad(key)))


# --- Extension-Protocol forwarding (BL-156) ---------------------------
#
# Like ACLStore, a bare EncryptedStore exposes only the core surface.
# Forwarding is conditional (``wrap_encrypted``) so isinstance stays
# truthful (ADR 0004 "don't fake it"). EncryptedStore transforms
# values, so each forwarded method seals/unseals; the AAD binds the
# namespace + key exactly as the core path.
#
# CASMemoryStore is deliberately NOT forwarded even when the inner
# store supports it: AES-GCM uses a fresh random nonce per write, so
# sealing ``expected`` yields different ciphertext every call and a
# value-equality compare-and-set against the stored ciphertext can
# never match; a read-modify-write emulation would not be atomic.
# Encryption over a CAS backend therefore drops CAS by design (a
# documented deviation, memory/README.md), rather than faking it.


class _EncBatchMixin:
    # _seal / _unseal are provided by EncryptedStore at composition;
    # declared as attributes (not empty-body methods) so the mixin
    # type-checks standalone and resolves to the real ones at runtime.
    _inner: BatchMemoryStore
    _seal: Callable[[str, bytes], bytes]
    _unseal: Callable[[str, bytes | None], bytes | None]

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        for k in keys:
            validate_key(k)
        sealed = await self._inner.mget(keys)
        return [self._unseal(k, s) for k, s in zip(keys, sealed, strict=True)]

    async def mset(self, items: Mapping[str, bytes], *, ttl_seconds: float | None = None) -> None:
        for k in items:
            validate_key(k)
        await self._inner.mset(
            {k: self._seal(k, v) for k, v in items.items()}, ttl_seconds=ttl_seconds
        )

    async def mdelete(self, keys: Sequence[str]) -> None:
        for k in keys:
            validate_key(k)
        await self._inner.mdelete(keys)


class _EncScanMixin:
    _inner: ScannableStore

    async def scan(
        self, *, cursor: str = "", prefix: str = "", count: int = 100
    ) -> tuple[str, list[str]]:
        # Keys are not encrypted (documented), so scan passes through.
        return await self._inner.scan(cursor=cursor, prefix=prefix, count=count)


class _EncContentMixin:
    _inner: MemoryStore
    _seal: Callable[[str, bytes], bytes]

    async def write_content(self, value: bytes, *, ttl_seconds: float | None = None) -> str:
        # The content key must hash the PLAINTEXT so identical content
        # dedupes; the inner store's own write_content would hash the
        # (nonce-randomised) ciphertext and never dedupe. Hash here,
        # then write the sealed value under that key.
        key = hashlib.sha256(value).hexdigest()
        sealed = self._seal(key, value)
        await self._inner.write(key, sealed, ttl_seconds=ttl_seconds)
        return key


class _EncSweepMixin:
    _inner: SweepableStore

    async def sweep_expired(self) -> int:
        return await self._inner.sweep_expired()


def wrap_encrypted(inner: MemoryStore, key_provider: KeyProvider) -> EncryptedStore:
    """EncryptedStore that also forwards the value-safe extension
    Protocols ``inner`` supports (BL-156).

    Forwards Batch / Scan / ContentAddressable / Sweepable conditionally
    (so ``isinstance`` is truthful). CASMemoryStore is intentionally not
    forwarded: GCM nonce randomisation makes ciphertext-equality CAS
    unrepresentable (see the module note and memory/README.md). Use
    this instead of constructing ``EncryptedStore`` directly over a
    capability-rich backend.
    """
    mixins: list[type] = []
    if isinstance(inner, BatchMemoryStore):
        mixins.append(_EncBatchMixin)
    if isinstance(inner, ScannableStore):
        mixins.append(_EncScanMixin)
    if isinstance(inner, ContentAddressableStore):
        mixins.append(_EncContentMixin)
    if isinstance(inner, SweepableStore):
        mixins.append(_EncSweepMixin)
    if not mixins:
        return EncryptedStore(inner, key_provider)
    cls = type("EncryptedStore", (EncryptedStore, *mixins), {})
    return cls(inner, key_provider)  # type: ignore[no-any-return]
