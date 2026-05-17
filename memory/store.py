"""MemoryStore Protocol and additive extension Protocols.

A MemoryStore is bound to a single Namespace at construction. The store
exposes an async key-value interface with bytes-on-the-wire semantics:
workloads serialize their data, the store handles raw bytes. Adapters
(InMemoryStore, RedisStore, SQLiteStore, ...) implement this Protocol.

Namespace isolation is structural, not policy: a workload that needs
two namespaces holds two MemoryStore instances. Cross-namespace access
is impossible without explicit construction.

The L1 surface (read/write/delete/list_keys) is intentionally minimal
(ADR 0004). L2 adds capabilities as *separate* Protocols that extend
MemoryStore, so a backend that cannot honour one (e.g. no atomic CAS)
simply does not implement it rather than faking it:

- BatchMemoryStore: mget / mset / mdelete (BL-081).
- ScannableStore: cursor-paged scan over very large keyspaces (BL-082).
- ContentAddressableStore: write_content -> sha256 key (BL-083).
- CASMemoryStore: compare-and-set / compare-and-delete (BL-072).
- SweepableStore: sweep_expired for the active TTL sweeper (BL-080).
- SemanticMemoryStore: vector write + similarity query (BL-131).
- VersionedMemoryStore: MVCC read/write/delete by version token
  (BL-124).

Audit (BL-040): an adapter MAY accept ``sink`` and
``base_event_fields`` at construction and emit MemoryRead / MemoryWrite
/ MemoryDelete. This follows the BudgetTracker / HarnessToolGuard
convention: emit only when base fields are supplied, so a store used
outside a contract run stays silent and dependency-free. The Protocol
does not mandate the constructor signature (constructors are not part
of a structural Protocol); it is a documented convention the reference
adapters follow.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from memory.types import Namespace

__all__ = [
    "BatchMemoryStore",
    "CASMemoryStore",
    "ContentAddressableStore",
    "MemoryStore",
    "ScannableStore",
    "SemanticHit",
    "SemanticMemoryStore",
    "SweepableStore",
    "VersionedMemoryStore",
]


@runtime_checkable
class MemoryStore(Protocol):
    """A namespace-bound key-value store.

    Implementations:
    - Are async-safe.
    - Validate keys via memory.validators.validate_key before any
      operation that takes a key.
    - Apply the namespace.retention_seconds as the default TTL when
      ttl_seconds is not provided to write().
    - Return None from read() for nonexistent or expired keys (do not
      raise).
    - Are idempotent on delete() of nonexistent keys (do not raise).
    - Return list_keys() sorted lexicographically, excluding expired
      keys.
    """

    name: str
    namespace: Namespace

    async def read(self, key: str) -> bytes | None: ...

    async def write(
        self,
        key: str,
        value: bytes,
        *,
        ttl_seconds: float | None = None,
    ) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def list_keys(self, prefix: str = "") -> list[str]: ...


@runtime_checkable
class BatchMemoryStore(MemoryStore, Protocol):
    """MemoryStore that also offers multi-key batch operations (BL-081).

    mget preserves input order, returning None per missing/expired key.
    mset applies one ttl_seconds to every item (None -> namespace
    default). mdelete is idempotent. Keys are validated like the
    single-key methods; one bad key fails the whole batch before any
    mutation (batches are all-or-nothing on validation).
    """

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]: ...

    async def mset(
        self,
        items: Mapping[str, bytes],
        *,
        ttl_seconds: float | None = None,
    ) -> None: ...

    async def mdelete(self, keys: Sequence[str]) -> None: ...


@runtime_checkable
class ScannableStore(MemoryStore, Protocol):
    """MemoryStore that supports cursor-paged key iteration (BL-082).

    scan returns ``(next_cursor, keys)``. An empty next_cursor means the
    iteration is exhausted. Pass the returned cursor back to continue.
    The opaque cursor is adapter-defined; callers must not parse it.
    Expired keys are excluded; ``count`` bounds the page size.
    """

    async def scan(
        self,
        *,
        cursor: str = "",
        prefix: str = "",
        count: int = 100,
    ) -> tuple[str, list[str]]: ...


@runtime_checkable
class ContentAddressableStore(MemoryStore, Protocol):
    """MemoryStore that supports content-addressed writes (BL-083).

    write_content stores ``value`` under the hex SHA-256 of its bytes
    and returns that key. Writing identical content is idempotent. The
    value is read back with the ordinary read(key).
    """

    async def write_content(
        self,
        value: bytes,
        *,
        ttl_seconds: float | None = None,
    ) -> str: ...


@runtime_checkable
class CASMemoryStore(MemoryStore, Protocol):
    """MemoryStore with optimistic-concurrency primitives (BL-072).

    Backends that expose native CAS (Redis WATCH/MULTI, DynamoDB
    ConditionExpression, SQLite transactions) implement this; others do
    not, rather than emulating it (ADR 0004). ``expected`` of None means
    "key must be absent". Returns True iff the swap/delete was applied.
    """

    async def compare_and_set(
        self,
        key: str,
        expected: bytes | None,
        new: bytes,
        *,
        ttl_seconds: float | None = None,
    ) -> bool: ...

    async def compare_and_delete(self, key: str, expected: bytes) -> bool: ...


@dataclass(frozen=True)
class SemanticHit:
    """One result of a similarity query (BL-131).

    ``score`` is cosine similarity in [-1, 1] (1.0 == identical
    direction); a non-finite component yields 0.0, the same guard as
    skills.embeddings.cosine_similarity (BL-159). ``value`` is the
    stored payload bytes for ``key``.
    """

    key: str
    score: float
    value: bytes


@runtime_checkable
class SemanticMemoryStore(MemoryStore, Protocol):
    """MemoryStore with vector write + similarity query (BL-131).

    A *separate* extension Protocol (ADR 0004 "don't fake it"): a
    backend implements it only if it can index embeddings, others do
    not. ``write_semantic`` stores ``value`` under ``key`` exactly like
    ``write`` and additionally indexes the embedding of ``text``;
    ``query_semantic`` embeds the query and returns up to ``k`` live
    hits ranked by descending cosine similarity. Expired keys are
    excluded (same TTL semantics as the core surface). The embedding
    model is the implementation's concern (it takes an Embedder), so
    the framework binds no vendor (ADR 0001), reusing the BL-110
    HashingEmbeddingProvider as the dependency-free default.
    """

    async def write_semantic(
        self,
        key: str,
        value: bytes,
        *,
        text: str,
        ttl_seconds: float | None = None,
    ) -> None: ...

    async def query_semantic(self, text: str, *, k: int = 5) -> list[SemanticHit]: ...


@runtime_checkable
class VersionedMemoryStore(MemoryStore, Protocol):
    """MVCC version-token concurrency, beyond CAS (BL-124, extends BL-072).

    A *separate* extension Protocol (ADR 0004 "don't fake it"). The
    version token is the SHA-256 of the stored value, so it is
    path-independent: any write that changes the value (via write,
    mset, compare_and_set, write_versioned, ...) changes the token, and
    identical content yields an identical token (content-version
    semantics; an ABA write of identical bytes is intentionally a
    no-conflict, the standard model). This lets a reader read a token,
    do work, and commit only if the value has not changed underneath
    it, without holding a lock.

    - ``read_versioned`` returns ``(value, token)`` or None (absent or
      expired, like ``read``).
    - ``write_versioned`` commits iff the live token equals
      ``expected_version`` (``None`` means "must be absent"); returns
      the new token on success or ``None`` on a version conflict.
    - ``delete_versioned`` deletes iff the live token equals
      ``expected_version``; returns whether it deleted.

    Multi-key transactions where the backend supports them are the
    documented remainder (the BL-072 scoping: Protocol + reference
    first, per-adapter breadth later). The token is over the stored
    bytes, so (like CAS) it is not forwarded through EncryptedStore: a
    per-write random GCM nonce makes the ciphertext token unstable.
    """

    async def read_versioned(self, key: str) -> tuple[bytes, str] | None: ...

    async def write_versioned(
        self,
        key: str,
        value: bytes,
        *,
        expected_version: str | None = None,
        ttl_seconds: float | None = None,
    ) -> str | None: ...

    async def delete_versioned(self, key: str, expected_version: str) -> bool: ...


@runtime_checkable
class SweepableStore(Protocol):
    """A store that can actively drop expired entries (BL-080).

    Lazy expiry (drop on access) is sufficient for correctness; this is
    a space optimisation for stores that accumulate write-once,
    never-read keys. ``sweep_expired`` removes every currently-expired
    entry and returns the count removed. memory.sweep.TTLSweeper drives
    it on an interval.
    """

    async def sweep_expired(self) -> int: ...
