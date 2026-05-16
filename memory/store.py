"""MemoryStore Protocol.

A MemoryStore is bound to a single Namespace at construction. The store
exposes an async key-value interface with bytes-on-the-wire semantics:
workloads serialize their data, the store handles raw bytes. Adapters
(InMemoryStore, RedisStore, S3Store, ...) implement this Protocol.

Namespace isolation is structural, not policy: a workload that needs
two namespaces holds two MemoryStore instances. Cross-namespace access
is impossible without explicit construction.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from memory.types import Namespace

__all__ = ["MemoryStore"]


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
