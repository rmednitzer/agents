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
- BoundedSweepableStore: SweepableStore + evict_to_capacity for a
  size-bound on the keyspace beyond age-only expiry (BL-212, the
  size-bound half of BL-135).
- SemanticMemoryStore: vector write + similarity query (BL-131).
- VersionedMemoryStore: MVCC read/write/delete by version token
  (BL-124, BL-180).
- TransactionalMemoryStore: atomic multi-key version-gated
  transactions on backends with native support (BL-180).

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
    "TransactionalMemoryStore",
    "TxnDelete",
    "TxnWrite",
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

    Best-effort give-up (BL-072 convention): on a backend that uses
    optimistic concurrency under the hood (Redis WATCH/MULTI), a hot
    key under sustained external contention may exhaust the bounded
    retry budget; ``write_versioned`` and ``delete_versioned`` then
    return ``None`` / ``False`` so a stuck retry loop cannot wedge the
    caller. The token surface alone does not let a caller distinguish
    a real version mismatch from contention exhaustion (matching
    ``compare_and_set``'s ``False`` return); a caller that needs that
    distinction should re-read the live token and decide.

    Multi-key transactions are now in scope as the separate
    ``TransactionalMemoryStore`` Protocol (BL-180), not part of this
    Protocol. The token is over the stored bytes, so (like CAS) it is
    not forwarded through EncryptedStore: a per-write random GCM
    nonce makes the ciphertext token unstable.
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


@dataclass(frozen=True)
class TxnWrite:
    """A conditional write within a multi-key transaction (BL-180).

    ``expected_version`` is the content-hash token the key must currently
    hold at commit time. ``None`` means "the key must be absent". A
    backend implementing TransactionalMemoryStore commits the write iff
    this precondition holds, atomically with every other operation in
    the transaction; otherwise the whole transaction is a no-op.
    ``ttl_seconds`` follows MemoryStore.write: ``None`` falls back to
    the namespace default.
    """

    value: bytes
    expected_version: str | None = None
    ttl_seconds: float | None = None


@dataclass(frozen=True)
class TxnDelete:
    """A conditional delete within a multi-key transaction (BL-180).

    ``expected_version`` must equal the key's current content-hash token
    at commit time. A delete with ``expected_version=None`` is not
    accepted: deleting a known-absent key is a no-op, not a transaction
    precondition; express "delete if exists with any version" by reading
    the live version first.
    """

    expected_version: str


@runtime_checkable
class TransactionalMemoryStore(MemoryStore, Protocol):
    """Atomic multi-key version-gated transactions (BL-180, extends BL-124).

    Builds on VersionedMemoryStore: every operation in a transaction
    carries an ``expected_version`` precondition referencing the same
    content-hash token. The transaction commits iff every precondition
    holds at commit time, atomically; otherwise it is a no-op and
    ``transact`` returns ``None``.

    Backend mappings:

    - ``InMemoryStore``: serialized by the store's ``asyncio.Lock``.
    - ``SQLiteStore``: ``BEGIN IMMEDIATE`` / per-key check / per-key
      apply / ``COMMIT``.
    - ``RedisStore``: ``WATCH(all keys)`` / read+verify / ``MULTI`` /
      commands / ``EXEC``, with bounded WatchError retries.
    - ``DynamoDBStore``: one ``TransactWriteItems`` call with a per-item
      ``ConditionExpression``; ``TransactionCanceledException`` whose
      reasons are all ``ConditionalCheckFailed`` is the no-op signal.

    Backends without native multi-key atomicity (S3) do not implement
    this Protocol; emulating it with per-key CAS would not be atomic in
    the face of concurrent writers (ADR 0004 "don't fake it"). The
    token is over the stored bytes, so (like CAS / VersionedMemoryStore)
    it is not forwarded through ``EncryptedStore`` (a per-write random
    GCM nonce makes the ciphertext token unstable).

    A key cannot appear in both ``writes`` and ``deletes``; the
    intersection is rejected at the contract boundary as a caller bug.

    Best-effort give-up (BL-072 convention): an optimistic-concurrency
    backend (Redis WATCH/MULTI) may exhaust its bounded retry budget on
    sustained external contention; ``transact`` then returns ``None``
    so a stuck retry loop cannot wedge the caller. The return surface
    alone does not let a caller distinguish a real precondition failure
    from contention exhaustion; a caller that needs the distinction
    should re-read live tokens via ``read_versioned`` and decide.
    """

    async def transact(
        self,
        *,
        writes: Mapping[str, TxnWrite] | None = None,
        deletes: Mapping[str, TxnDelete] | None = None,
    ) -> dict[str, str] | None:
        """Atomically commit the writes and deletes.

        Returns ``{key: new_token}`` for each written key on success, or
        ``None`` on any precondition failure (no partial application).
        An empty transaction (``writes`` and ``deletes`` both empty / None)
        returns an empty dict. See the class docstring for the
        best-effort give-up on retry-budget exhaustion.
        """
        ...


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


@runtime_checkable
class BoundedSweepableStore(SweepableStore, Protocol):
    """A SweepableStore that also enforces a size bound on its keyspace
    (BL-212, the size-bound half of BL-135).

    Lazy expiry plus age-only ``sweep_expired`` is sufficient for
    correctness; the bound is a space cap for long-horizon workloads
    whose write rate outpaces their TTL (or whose entries have no TTL
    and are written once, read many). ``evict_to_capacity`` removes the
    oldest entries (in insertion order, equivalent to FIFO by first
    write of a given key) until the live keyspace is at most
    ``max_keys``, returning the count evicted. ``memory.sweep.TTLSweeper``
    can drive it on an interval after the age-only pass when its
    ``max_keys`` kwarg is set.

    The eviction order is insertion order, not LRU: an in-place
    overwrite of an existing key keeps the original position, matching
    Python's dict semantics. A backend wanting strict last-write-out
    FIFO must delete-then-write on overwrite (the InMemoryStore
    reference takes the dict-native ordering; durable adapters
    document their own ordering when they implement this Protocol).

    Returns the number of entries removed; the call is a no-op (returns
    0) when the live keyspace is already at or under ``max_keys``.
    ``max_keys`` must be positive (zero would clear the store, not
    bound it; the caller can ``mdelete`` the whole keyspace if that is
    the intent). The reference InMemoryStore raises ``ValueError`` on
    a non-positive cap; a backend MAY raise the same.
    """

    async def evict_to_capacity(self, max_keys: int) -> int: ...
