"""RedisStore: production MemoryStore adapter (BL-030, ADR 0007).

``redis`` is an optional dependency: this module imports it lazily so
the core package works without it. Install the extra:
``pip install 'agents[redis]'``.

Design:

- Native TTL via ``SET ... PX`` (millisecond precision). No lazy-expiry
  bookkeeping and no SweepableStore: Redis evicts expired keys itself.
- Namespace isolation by key prefix ``"<namespace>::"``. ``validate_key``
  forbids ``"::"`` in user keys, so the internal separator can never
  collide with or be forged by a caller's key.
- Batch ops pipeline (MGET / pipelined SET / DEL).
- ``scan`` wraps Redis SCAN; the integer cursor is returned as the
  opaque string cursor ("" when the iteration is complete). Per-page
  ordering is Redis-defined, which the ScannableStore contract permits.
- CAS via the canonical WATCH/MULTI optimistic transaction with bounded
  retries; on persistent contention it returns False (best-effort) so a
  hot key cannot wedge the caller. ``VersionedMemoryStore`` (BL-180)
  reuses the same WATCH/MULTI loop with a content-hash version check.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from memory._audit import MemoryAudit
from memory.store import TxnDelete, TxnWrite
from memory.types import Namespace
from memory.validators import validate_key

__all__ = ["RedisStore"]

_CAS_MAX_RETRIES = 50


class RedisStore:
    """Production MemoryStore backed by Redis (redis.asyncio)."""

    name: str = "redis"

    def __init__(
        self,
        namespace: Namespace,
        url: str = "redis://localhost:6379/0",
        *,
        client: Any | None = None,
        sink: Any | None = None,
        base_event_fields: dict[str, Any] | None = None,
    ) -> None:
        if client is None:
            try:
                import redis.asyncio as aioredis
            except ImportError as exc:  # pragma: no cover - env dependent
                raise ImportError(
                    "RedisStore requires the 'redis' extra: pip install 'agents[redis]'"
                ) from exc
            client = aioredis.from_url(url)
        self._r = client
        self._namespace = namespace
        self._prefix = f"{namespace.name}::"
        self._audit = MemoryAudit(namespace.name, sink, base_event_fields)

    @property
    def namespace(self) -> Namespace:
        return self._namespace

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def _strip(self, full: str) -> str:
        return full[len(self._prefix) :]

    def _ttl(self, ttl_seconds: float | None) -> float | None:
        return ttl_seconds if ttl_seconds is not None else self._namespace.retention_seconds

    @staticmethod
    def _b(value: Any) -> bytes | None:
        if value is None:
            return None
        return value if isinstance(value, bytes) else str(value).encode()

    async def read(self, key: str) -> bytes | None:
        validate_key(key)
        value = self._b(await self._r.get(self._k(key)))
        self._audit.read(key, hit=value is not None)
        return value

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        validate_key(key)
        ttl = self._ttl(ttl_seconds)
        if ttl is not None:
            await self._r.set(self._k(key), value, px=max(1, int(ttl * 1000)))
        else:
            await self._r.set(self._k(key), value)
        self._audit.write(key, value_bytes=len(value), ttl_seconds=ttl)

    async def delete(self, key: str) -> None:
        validate_key(key)
        existed = bool(await self._r.delete(self._k(key)))
        self._audit.delete(key, existed=existed)

    async def list_keys(self, prefix: str = "") -> list[str]:
        match = f"{self._prefix}{prefix}*"
        out: list[str] = []
        cursor = 0
        while True:
            cursor, batch = await self._r.scan(cursor=cursor, match=match, count=500)
            out.extend(self._strip(k.decode() if isinstance(k, bytes) else k) for k in batch)
            if cursor == 0:
                break
        return sorted(out)

    async def mget(self, keys: list[str]) -> list[bytes | None]:
        for k in keys:
            validate_key(k)
        if not keys:
            return []
        raw = await self._r.mget([self._k(k) for k in keys])
        values = [self._b(v) for v in raw]
        for k, v in zip(keys, values, strict=True):
            self._audit.read(k, hit=v is not None)
        return values

    async def mset(self, items: dict[str, bytes], *, ttl_seconds: float | None = None) -> None:
        for k in items:
            validate_key(k)
        ttl = self._ttl(ttl_seconds)
        pipe = self._r.pipeline(transaction=False)
        for k, v in items.items():
            if ttl is not None:
                pipe.set(self._k(k), v, px=max(1, int(ttl * 1000)))
            else:
                pipe.set(self._k(k), v)
        await pipe.execute()
        for k, v in items.items():
            self._audit.write(k, value_bytes=len(v), ttl_seconds=ttl)

    async def mdelete(self, keys: list[str]) -> None:
        for k in keys:
            validate_key(k)
        if not keys:
            return
        pipe = self._r.pipeline(transaction=False)
        for k in keys:
            pipe.delete(self._k(k))
        results = await pipe.execute()
        for k, existed in zip(keys, results, strict=True):
            self._audit.delete(k, existed=bool(existed))

    async def scan(
        self, *, cursor: str = "", prefix: str = "", count: int = 100
    ) -> tuple[str, list[str]]:
        if count <= 0:
            return "", []
        start = int(cursor) if cursor else 0
        next_cursor, batch = await self._r.scan(
            cursor=start, match=f"{self._prefix}{prefix}*", count=count
        )
        keys = sorted(self._strip(k.decode() if isinstance(k, bytes) else k) for k in batch)
        return ("" if next_cursor == 0 else str(next_cursor)), keys

    async def write_content(self, value: bytes, *, ttl_seconds: float | None = None) -> str:
        key = hashlib.sha256(value).hexdigest()
        await self.write(key, value, ttl_seconds=ttl_seconds)
        return key

    # --- VersionedMemoryStore (BL-124, BL-180) ------------------------

    @staticmethod
    def _token(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    async def compare_and_set(
        self,
        key: str,
        expected: bytes | None,
        new: bytes,
        *,
        ttl_seconds: float | None = None,
    ) -> bool:
        validate_key(key)
        ttl = self._ttl(ttl_seconds)
        from redis import WatchError

        rk = self._k(key)
        for _ in range(_CAS_MAX_RETRIES):
            async with self._r.pipeline() as pipe:
                try:
                    await pipe.watch(rk)
                    current = self._b(await pipe.get(rk))
                    if current != expected:
                        await pipe.unwatch()
                        return False
                    pipe.multi()
                    if ttl is not None:
                        pipe.set(rk, new, px=max(1, int(ttl * 1000)))
                    else:
                        pipe.set(rk, new)
                    await pipe.execute()
                except WatchError:
                    continue  # key changed under us; retry
                self._audit.write(key, value_bytes=len(new), ttl_seconds=ttl)
                return True
        return False  # persistent contention; best-effort give up

    async def compare_and_delete(self, key: str, expected: bytes) -> bool:
        validate_key(key)
        from redis import WatchError

        rk = self._k(key)
        for _ in range(_CAS_MAX_RETRIES):
            async with self._r.pipeline() as pipe:
                try:
                    await pipe.watch(rk)
                    current = self._b(await pipe.get(rk))
                    if current != expected:
                        await pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.delete(rk)
                    await pipe.execute()
                except WatchError:
                    continue
                self._audit.delete(key, existed=True)
                return True
        return False

    async def read_versioned(self, key: str) -> tuple[bytes, str] | None:
        validate_key(key)
        value = self._b(await self._r.get(self._k(key)))
        self._audit.read(key, hit=value is not None)
        if value is None:
            return None
        return value, self._token(value)

    async def write_versioned(
        self,
        key: str,
        value: bytes,
        *,
        expected_version: str | None = None,
        ttl_seconds: float | None = None,
    ) -> str | None:
        # WATCH/MULTI mirror of compare_and_set: the precondition is the
        # content-hash of the live value (path-independent, per the
        # VersionedMemoryStore contract), not a bytes-equality check.
        # Persistent WatchError contention exhausts the retry budget and
        # returns None (the BL-072 CAS best-effort give-up; a hot key
        # cannot wedge the caller).
        validate_key(key)
        ttl = self._ttl(ttl_seconds)
        from redis import WatchError

        rk = self._k(key)
        for _ in range(_CAS_MAX_RETRIES):
            async with self._r.pipeline() as pipe:
                try:
                    await pipe.watch(rk)
                    current = self._b(await pipe.get(rk))
                    live_version = None if current is None else self._token(current)
                    if live_version != expected_version:
                        await pipe.unwatch()
                        return None
                    pipe.multi()
                    if ttl is not None:
                        pipe.set(rk, value, px=max(1, int(ttl * 1000)))
                    else:
                        pipe.set(rk, value)
                    await pipe.execute()
                except WatchError:
                    continue
                self._audit.write(key, value_bytes=len(value), ttl_seconds=ttl)
                return self._token(value)
        return None

    async def delete_versioned(self, key: str, expected_version: str) -> bool:
        validate_key(key)
        from redis import WatchError

        rk = self._k(key)
        for _ in range(_CAS_MAX_RETRIES):
            async with self._r.pipeline() as pipe:
                try:
                    await pipe.watch(rk)
                    current = self._b(await pipe.get(rk))
                    if current is None or self._token(current) != expected_version:
                        await pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.delete(rk)
                    await pipe.execute()
                except WatchError:
                    continue
                self._audit.delete(key, existed=True)
                return True
        return False

    # --- TransactionalMemoryStore (BL-180) ----------------------------

    async def transact(
        self,
        *,
        writes: Mapping[str, TxnWrite] | None = None,
        deletes: Mapping[str, TxnDelete] | None = None,
    ) -> dict[str, str] | None:
        writes_d = dict(writes or {})
        deletes_d = dict(deletes or {})
        overlap = set(writes_d) & set(deletes_d)
        if overlap:
            raise ValueError(f"transaction key in both writes and deletes: {sorted(overlap)}")
        for k in (*writes_d, *deletes_d):
            validate_key(k)
        if not writes_d and not deletes_d:
            return {}
        from redis import WatchError

        all_rkeys = [self._k(k) for k in (*writes_d, *deletes_d)]
        for _ in range(_CAS_MAX_RETRIES):
            async with self._r.pipeline() as pipe:
                try:
                    await pipe.watch(*all_rkeys)
                    # Verify every precondition with sequential GETs (no
                    # MGET inside a WATCH/MULTI: GET runs immediately so
                    # we can inspect the value before MULTI starts the
                    # queued command block).
                    for key, w in writes_d.items():
                        current = self._b(await pipe.get(self._k(key)))
                        live = None if current is None else self._token(current)
                        if live != w.expected_version:
                            await pipe.unwatch()
                            return None
                    for key, d in deletes_d.items():
                        current = self._b(await pipe.get(self._k(key)))
                        if current is None or self._token(current) != d.expected_version:
                            await pipe.unwatch()
                            return None
                    pipe.multi()
                    for key, w in writes_d.items():
                        ttl = self._ttl(w.ttl_seconds)
                        if ttl is not None:
                            pipe.set(self._k(key), w.value, px=max(1, int(ttl * 1000)))
                        else:
                            pipe.set(self._k(key), w.value)
                    for key in deletes_d:
                        pipe.delete(self._k(key))
                    await pipe.execute()
                except WatchError:
                    continue  # any watched key changed; retry
                out = {key: self._token(w.value) for key, w in writes_d.items()}
                for key, w in writes_d.items():
                    self._audit.write(
                        key,
                        value_bytes=len(w.value),
                        ttl_seconds=self._ttl(w.ttl_seconds),
                    )
                for key in deletes_d:
                    self._audit.delete(key, existed=True)
                return out
        return None  # persistent contention; best-effort give up
