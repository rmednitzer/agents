"""InMemoryStore: reference MemoryStore implementation.

A single-process in-memory key-value store with TTL support and
asyncio.Lock-serialized writes (last-write-wins on concurrent writes
within the same store instance).

It implements every L2 extension Protocol (batch, scan,
content-addressing, CAS, sweep) and the optional audit-event surface,
so it doubles as the reference for adapter authors and the deterministic
backend for tests.

Not suitable for production multi-process workloads. Use for tests,
local development, and as a reference for adapter implementations.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from harness.sinks import EventSink
from memory._audit import MemoryAudit
from memory.types import Namespace
from memory.validators import validate_key

__all__ = ["InMemoryStore"]


@dataclass
class _Entry:
    """One stored value plus its absolute expiry timestamp (None = no expiry)."""

    value: bytes
    expires_at: float | None


class InMemoryStore:
    """In-process MemoryStore reference implementation.

    Optionally audited: pass ``sink`` and ``base_event_fields`` (the
    workload / contract / contract_version / trace_id / span_id of the
    surrounding run, exactly as BudgetTracker and HarnessToolGuard take
    them) to emit MemoryRead / MemoryWrite / MemoryDelete. With no base
    fields the store stays silent, so it is usable standalone.
    """

    name: str = "in-memory"

    def __init__(
        self,
        namespace: Namespace,
        *,
        sink: EventSink | None = None,
        base_event_fields: dict[str, Any] | None = None,
    ) -> None:
        self._namespace = namespace
        self._data: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()
        self._audit = MemoryAudit(namespace.name, sink, base_event_fields)

    @property
    def namespace(self) -> Namespace:
        return self._namespace

    def _effective_ttl(self, ttl_seconds: float | None) -> float | None:
        return ttl_seconds if ttl_seconds is not None else self._namespace.retention_seconds

    def _live_value(self, key: str, now: float) -> bytes | None:
        """Return the value if present and unexpired, dropping it if expired.

        Caller must hold the lock.
        """
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.expires_at is not None and now > entry.expires_at:
            del self._data[key]
            return None
        return entry.value

    # --- core MemoryStore ---------------------------------------------

    async def read(self, key: str) -> bytes | None:
        validate_key(key)
        async with self._lock:
            value = self._live_value(key, time.time())
        self._audit.read(key, hit=value is not None)
        return value

    async def write(
        self,
        key: str,
        value: bytes,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        validate_key(key)
        effective_ttl = self._effective_ttl(ttl_seconds)
        expires_at = time.time() + effective_ttl if effective_ttl is not None else None
        async with self._lock:
            self._data[key] = _Entry(value=value, expires_at=expires_at)
        self._audit.write(key, value_bytes=len(value), ttl_seconds=effective_ttl)

    async def delete(self, key: str) -> None:
        validate_key(key)
        async with self._lock:
            # An expired-but-unswept entry is semantically absent
            # (read/mget treat it as a miss); the audit must agree.
            existed = self._live_value(key, time.time()) is not None
            self._data.pop(key, None)
        self._audit.delete(key, existed=existed)

    async def list_keys(self, prefix: str = "") -> list[str]:
        async with self._lock:
            now = time.time()
            live = [
                k
                for k, entry in self._data.items()
                if entry.expires_at is None or entry.expires_at > now
            ]
            return sorted(k for k in live if k.startswith(prefix))

    # --- BatchMemoryStore (BL-081) ------------------------------------

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        for k in keys:
            validate_key(k)
        async with self._lock:
            now = time.time()
            values = [self._live_value(k, now) for k in keys]
        for k, v in zip(keys, values, strict=True):
            self._audit.read(k, hit=v is not None)
        return values

    async def mset(
        self,
        items: Mapping[str, bytes],
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        for k in items:
            validate_key(k)
        effective_ttl = self._effective_ttl(ttl_seconds)
        expires_at = time.time() + effective_ttl if effective_ttl is not None else None
        async with self._lock:
            for k, v in items.items():
                self._data[k] = _Entry(value=v, expires_at=expires_at)
        for k, v in items.items():
            self._audit.write(k, value_bytes=len(v), ttl_seconds=effective_ttl)

    async def mdelete(self, keys: Sequence[str]) -> None:
        for k in keys:
            validate_key(k)
        async with self._lock:
            now = time.time()
            existed: dict[str, bool] = {}
            for k in keys:
                existed[k] = self._live_value(k, now) is not None
                self._data.pop(k, None)
        for k, did in existed.items():
            self._audit.delete(k, existed=did)

    # --- ScannableStore (BL-082) --------------------------------------

    async def scan(
        self,
        *,
        cursor: str = "",
        prefix: str = "",
        count: int = 100,
    ) -> tuple[str, list[str]]:
        if count <= 0:
            return "", []
        async with self._lock:
            now = time.time()
            candidates = sorted(
                k
                for k, entry in self._data.items()
                if (entry.expires_at is None or entry.expires_at > now)
                and k.startswith(prefix)
                and (cursor == "" or k > cursor)
            )
        page = candidates[:count]
        next_cursor = page[-1] if len(candidates) > count else ""
        return next_cursor, page

    # --- ContentAddressableStore (BL-083) -----------------------------

    async def write_content(
        self,
        value: bytes,
        *,
        ttl_seconds: float | None = None,
    ) -> str:
        key = hashlib.sha256(value).hexdigest()
        await self.write(key, value, ttl_seconds=ttl_seconds)
        return key

    # --- CASMemoryStore (BL-072) --------------------------------------

    async def compare_and_set(
        self,
        key: str,
        expected: bytes | None,
        new: bytes,
        *,
        ttl_seconds: float | None = None,
    ) -> bool:
        validate_key(key)
        effective_ttl = self._effective_ttl(ttl_seconds)
        async with self._lock:
            current = self._live_value(key, time.time())
            if current != expected:
                return False
            expires_at = time.time() + effective_ttl if effective_ttl is not None else None
            self._data[key] = _Entry(value=new, expires_at=expires_at)
        self._audit.write(key, value_bytes=len(new), ttl_seconds=effective_ttl)
        return True

    async def compare_and_delete(self, key: str, expected: bytes) -> bool:
        validate_key(key)
        async with self._lock:
            current = self._live_value(key, time.time())
            if current != expected:
                return False
            del self._data[key]
        self._audit.delete(key, existed=True)
        return True

    # --- SweepableStore (BL-080) --------------------------------------

    async def sweep_expired(self) -> int:
        async with self._lock:
            now = time.time()
            expired = [
                k
                for k, entry in self._data.items()
                if entry.expires_at is not None and now > entry.expires_at
            ]
            for k in expired:
                del self._data[k]
            return len(expired)
