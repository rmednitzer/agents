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

KeyProvider abstracts key sourcing. ``StaticKeyProvider``,
``EnvKeyProvider`` and ``FileKeyProvider`` (BL-111) are single-key,
dependency-free sources. ``VersionedKeyProvider`` (BL-111) is the
extension point for rotation and for a KMS: it exposes a *current*
versioned key for new writes plus historical keys by id for decrypt,
so rotating does not strand old ciphertext. A KMS-backed provider is a
few lines satisfying that Protocol with its SDK imported lazily, kept
out-of-tree by the same no-vendor-binding stance as ADR 0001 (mirrors
``HashingEmbeddingProvider``, BL-110). ``RotatingKeyProvider`` is the
in-tree reference implementation.

Migration (BL-181): a store sealed by a plain ``KeyProvider`` holds
bare ``nonce+ct`` with no envelope to distinguish it from a versioned
value. When a ``VersionedKeyProvider`` is adopted on an existing store,
a value that does not decrypt as the envelope is retried as legacy
``nonce+ct`` with the provider's CURRENT key. AES-GCM authentication
makes a wrong key or format fail (it never returns a wrong plaintext);
if both interpretations fail the original, more informative error is
raised. The migration contract: seed the ring with the existing key as
the current version (rotate later, once values have been rewritten);
data still under a key the provider has rotated away from is not
reachable for legacy reads (``LIMITATIONS.md`` L16).
"""

from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol, cast, runtime_checkable

from memory.store import (
    BatchMemoryStore,
    ContentAddressableStore,
    MemoryStore,
    ScannableStore,
    SweepableStore,
)
from memory.types import Namespace
from memory.validators import validate_key

__all__ = [
    "EncryptedStore",
    "EnvKeyProvider",
    "FileKeyProvider",
    "KeyProvider",
    "RotatingKeyProvider",
    "StaticKeyProvider",
    "VersionedKeyProvider",
    "wrap_encrypted",
]

_NONCE_BYTES = 12
_KeyEncoding = Literal["base64", "hex", "raw"]


@runtime_checkable
class KeyProvider(Protocol):
    """Sources the symmetric key for a namespace.

    Implementations may return a process-static key, fetch from a KMS,
    or rotate; the contract is only that ``key_for`` returns a 32-byte
    (AES-256) key for the namespace.
    """

    def key_for(self, namespace: str) -> bytes: ...


@runtime_checkable
class VersionedKeyProvider(Protocol):
    """A KeyProvider with rotation/versioning (BL-111).

    ``current_key`` returns ``(key_id, key)`` used to seal new writes;
    ``key`` resolves a historical version by id so ciphertext written
    under a previous key still decrypts after a rotation. An
    EncryptedStore built over a versioned provider stamps the sealing
    ``key_id`` into the value envelope; one built over a plain
    KeyProvider keeps the exact prior on-disk format (no envelope), so
    this is fully additive. A KMS-backed provider implements this
    Protocol (per-namespace keys, SDK imported lazily) and stays
    out-of-tree (ADR 0001 no-vendor-binding).
    """

    def current_key(self, namespace: str) -> tuple[str, bytes]: ...

    def key(self, namespace: str, key_id: str) -> bytes: ...


def _validate_key_bytes(key: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("AES-256 key must be exactly 32 bytes")
    return key


def _decode_key(raw: bytes, encoding: _KeyEncoding) -> bytes:
    # A key from an env var / file is config at a trust boundary:
    # surface a clear ValueError naming the expected encoding rather
    # than a low-level binascii/UnicodeDecodeError, matching the
    # controlled errors EnvKeyProvider/FileKeyProvider already raise.
    if encoding == "raw":
        return _validate_key_bytes(raw)
    try:
        text = raw.decode().strip()
        decoded = (
            bytes.fromhex(text) if encoding == "hex" else base64.b64decode(text, validate=True)
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"key is not valid {encoding}: {exc}") from exc
    return _validate_key_bytes(decoded)


class StaticKeyProvider:
    """A fixed 32-byte key for every namespace (dev/tests/single-tenant)."""

    def __init__(self, key: bytes) -> None:
        self._key = _validate_key_bytes(key)

    def key_for(self, namespace: str) -> bytes:
        return self._key


class EnvKeyProvider:
    """Reads the symmetric key from an environment variable (BL-111).

    ``var`` holds the key, ``encoding`` selects how it is decoded
    (base64 default, hex, or raw bytes). One key for every namespace,
    like StaticKeyProvider; the variable is read on each ``key_for`` so
    a process that re-exports it is picked up. A missing variable is a
    clear ValueError, not a silent fallback.
    """

    def __init__(
        self, var: str = "AGENTS_MEMORY_KEY", *, encoding: _KeyEncoding = "base64"
    ) -> None:
        self._var = var
        self._encoding: _KeyEncoding = encoding

    def key_for(self, namespace: str) -> bytes:
        raw = os.environ.get(self._var)
        if raw is None:
            raise ValueError(f"environment variable {self._var!r} is not set")
        return _decode_key(raw.encode(), self._encoding)


class FileKeyProvider:
    """Reads the symmetric key from a file (BL-111).

    ``encoding`` selects raw 32 bytes (default), base64, or hex text.
    The file is read on each ``key_for`` so an out-of-band key roll on
    disk is picked up without restarting the process.
    """

    def __init__(self, path: str | Path, *, encoding: _KeyEncoding = "raw") -> None:
        self._path = Path(path)
        self._encoding: _KeyEncoding = encoding

    def key_for(self, namespace: str) -> bytes:
        if not self._path.is_file():
            raise ValueError(f"key file {str(self._path)!r} does not exist")
        return _decode_key(self._path.read_bytes(), self._encoding)


class RotatingKeyProvider:
    """In-tree reference VersionedKeyProvider (BL-111).

    Holds an id -> 32-byte-key ring and a current id. ``rotate`` adds a
    new version and makes it current; prior versions stay resolvable by
    ``key`` so already-sealed values still decrypt. One ring for every
    namespace (like StaticKeyProvider); a per-namespace or KMS-backed
    provider is just another VersionedKeyProvider.
    """

    def __init__(self, keys: Mapping[str, bytes], current: str) -> None:
        if not keys:
            raise ValueError("at least one key version is required")
        self._keys = {kid: _validate_key_bytes(k) for kid, k in keys.items()}
        if current not in self._keys:
            raise ValueError(f"current version {current!r} not in the key ring")
        self._current = current

    def rotate(self, key_id: str, key: bytes) -> None:
        """Add ``key_id`` -> ``key`` and make it the current version."""
        if key_id in self._keys:
            raise ValueError(f"key version {key_id!r} already exists")
        self._keys[key_id] = _validate_key_bytes(key)
        self._current = key_id

    def current_key(self, namespace: str) -> tuple[str, bytes]:
        return self._current, self._keys[self._current]

    def key(self, namespace: str, key_id: str) -> bytes:
        try:
            return self._keys[key_id]
        except KeyError:
            raise KeyError(f"unknown key version {key_id!r}") from None


class EncryptedStore:
    """Wraps a MemoryStore, sealing values with AES-256-GCM.

    The wrapped store is the source of truth for the namespace, key
    validation, TTL, and audit; this decorator only transforms the
    value bytes. It satisfies the MemoryStore Protocol so it composes
    anywhere a store is expected (including under ACLStore).
    """

    name: str = "encrypted"

    def __init__(
        self, inner: MemoryStore, key_provider: KeyProvider | VersionedKeyProvider
    ) -> None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ImportError(
                "EncryptedStore requires the 'crypto' extra: pip install 'agents[crypto]'"
            ) from exc
        self._inner = inner
        self._aesgcm = AESGCM
        self._ns = inner.namespace.name
        # A versioned provider switches the value format to a key-id
        # envelope so a rotation does not strand prior ciphertext; a
        # plain KeyProvider keeps the exact prior on-disk format (BL-111,
        # additive: omitting a versioned provider is byte-identical to
        # before).
        self._versioned = isinstance(key_provider, VersionedKeyProvider)
        if self._versioned:
            self._vkp = cast(VersionedKeyProvider, key_provider)
            self._aes_cache: dict[str, Any] = {}
        else:
            # The negative branch of a runtime_checkable Protocol
            # isinstance does not narrow the union for mypy; in this
            # branch the provider is the plain (key_for) KeyProvider.
            self._aes = AESGCM(cast(KeyProvider, key_provider).key_for(self._ns))

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

    def _aes_cached(self, key_id: str, key_bytes: bytes) -> Any:
        """AESGCM for ``key_id``, caching the given bytes (no re-fetch)."""
        aes = self._aes_cache.get(key_id)
        if aes is None:
            aes = self._aesgcm(key_bytes)
            self._aes_cache[key_id] = aes
        return aes

    def _aes_for(self, key_id: str) -> Any:
        aes = self._aes_cache.get(key_id)
        if aes is None:
            # Only reached for a historical key id on decrypt; the
            # current key is cached by _seal from current_key()'s bytes.
            aes = self._aesgcm(self._vkp.key(self._ns, key_id))
            self._aes_cache[key_id] = aes
        return aes

    def _seal(self, key: str, value: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        aad = self._aad(key)
        if not self._versioned:
            return nonce + bytes(self._aes.encrypt(nonce, value, aad))
        # Versioned envelope: 1-byte key-id length, key-id, nonce, ct.
        # The key id is NOT in the AAD; AAD stays namespace::key so a
        # value re-sealed under a rotated key still binds to the same
        # namespace/key. The id only selects the decrypt key. Use the
        # key bytes current_key() already returned (do not call
        # provider.key() again: for a KMS provider that would double
        # the lookup per write).
        key_id, key_bytes = self._vkp.current_key(self._ns)
        kid = key_id.encode()
        if not 0 < len(kid) < 256:
            raise ValueError("key id must be 1..255 bytes when UTF-8 encoded")
        aes = self._aes_cached(key_id, key_bytes)
        ct = bytes(aes.encrypt(nonce, value, aad))
        return bytes([len(kid)]) + kid + nonce + ct

    def _decrypt_envelope(self, sealed: bytes, aad: bytes) -> bytes:
        """Decrypt the versioned [len][key-id][nonce][ct] envelope.

        Raises ValueError (malformed/truncated/bad-id), KeyError (the
        envelope's key version is not in the provider), or InvalidTag
        (authentication failure) so the caller can decide whether to
        try the legacy fallback.
        """
        if not sealed:
            raise ValueError("malformed encrypted envelope: empty value")
        n = sealed[0]
        if n == 0 or len(sealed) < 1 + n + _NONCE_BYTES:
            raise ValueError("malformed encrypted envelope: truncated header")
        try:
            key_id = sealed[1 : 1 + n].decode()
        except UnicodeDecodeError as exc:
            raise ValueError("malformed encrypted envelope: bad key id") from exc
        body = sealed[1 + n :]
        nonce, ct = body[:_NONCE_BYTES], body[_NONCE_BYTES:]
        return bytes(self._aes_for(key_id).decrypt(nonce, ct, aad))

    def _unseal(self, key: str, sealed: bytes | None) -> bytes | None:
        if sealed is None:
            return None
        aad = self._aad(key)
        if not self._versioned:
            nonce, ct = sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:]
            return bytes(self._aes.decrypt(nonce, ct, aad))
        from cryptography.exceptions import InvalidTag

        try:
            return self._decrypt_envelope(sealed, aad)
        except (ValueError, KeyError, InvalidTag) as env_err:
            # Authenticated legacy fallback (BL-181): a store previously
            # sealed by a plain KeyProvider holds bare nonce+ct, with no
            # envelope to distinguish it. Retry as legacy with the
            # versioned provider's CURRENT key. AES-GCM authentication
            # makes a wrong key/format fail (InvalidTag), so this never
            # returns a wrong plaintext; if both interpretations fail
            # the original (more informative, e.g. unknown-key-version)
            # error is raised. Migration contract: seed the ring with
            # the existing key as the current version, rotate later
            # (memory/README.md, LIMITATIONS.md L16).
            cur_id, cur_bytes = self._vkp.current_key(self._ns)
            try:
                nonce, ct = sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:]
                return bytes(self._aes_cached(cur_id, cur_bytes).decrypt(nonce, ct, aad))
            except (InvalidTag, ValueError):
                # InvalidTag: wrong key/not legacy. ValueError: the
                # bytes are too short for even a nonce (a truly
                # malformed value). Either way the original, more
                # informative envelope error is the right one to raise.
                raise env_err from None


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


def wrap_encrypted(
    inner: MemoryStore, key_provider: KeyProvider | VersionedKeyProvider
) -> EncryptedStore:
    """EncryptedStore that also forwards the value-safe extension
    Protocols ``inner`` supports (BL-156).

    Forwards Batch / Scan / ContentAddressable / Sweepable conditionally
    (so ``isinstance`` is truthful). CASMemoryStore is intentionally not
    forwarded: GCM nonce randomisation makes ciphertext-equality CAS
    unrepresentable (see the module note and memory/README.md). Use
    this instead of constructing ``EncryptedStore`` directly over a
    capability-rich backend. A ``VersionedKeyProvider`` (BL-111) is
    accepted and enables the rotation-safe value envelope.
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
