"""InMemoryStore: reference MemoryStore implementation.

A single-process in-memory key-value store with TTL support and
asyncio.Lock-serialized writes (last-write-wins on concurrent writes
within the same store instance).

Not suitable for production multi-process workloads. Use for tests,
local development, and as a reference for adapter implementations.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from memory.types import Namespace
from memory.validators import validate_key

__all__ = ["InMemoryStore"]


@dataclass
class _Entry:
    """One stored value plus its absolute expiry timestamp (None = no expiry)."""

    value: bytes
    expires_at: float | None


class InMemoryStore:
    """In-process MemoryStore reference implementation."""

    name: str = "in-memory"

    def __init__(self, namespace: Namespace) -> None:
        self._namespace = namespace
        self._data: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    @property
    def namespace(self) -> Namespace:
        return self._namespace

    async def read(self, key: str) -> bytes | None:
        validate_key(key)
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.expires_at is not None and time.time() > entry.expires_at:
                # Lazy expiry: drop the entry on read.
                del self._data[key]
                return None
            return entry.value

    async def write(
        self,
        key: str,
        value: bytes,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        validate_key(key)
        effective_ttl = (
            ttl_seconds if ttl_seconds is not None else self._namespace.retention_seconds
        )
        expires_at = time.time() + effective_ttl if effective_ttl is not None else None
        async with self._lock:
            self._data[key] = _Entry(value=value, expires_at=expires_at)

    async def delete(self, key: str) -> None:
        validate_key(key)
        async with self._lock:
            self._data.pop(key, None)

    async def list_keys(self, prefix: str = "") -> list[str]:
        async with self._lock:
            now = time.time()
            live = [
                k
                for k, entry in self._data.items()
                if entry.expires_at is None or entry.expires_at > now
            ]
            return sorted(k for k in live if k.startswith(prefix))
