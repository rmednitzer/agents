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

import os
from typing import Protocol, runtime_checkable

from memory.store import MemoryStore
from memory.types import Namespace

__all__ = ["EncryptedStore", "KeyProvider", "StaticKeyProvider"]

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
        sealed = await self._inner.read(key)
        if sealed is None:
            return None
        nonce, ct = sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:]
        return bytes(self._aes.decrypt(nonce, ct, self._aad(key)))

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = nonce + self._aes.encrypt(nonce, value, self._aad(key))
        await self._inner.write(key, sealed, ttl_seconds=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._inner.delete(key)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return await self._inner.list_keys(prefix)
