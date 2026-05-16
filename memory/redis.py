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
  hot key cannot wedge the caller.
"""

from __future__ import annotations

from typing import Any

from memory._audit import MemoryAudit
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
            await self._r.set(self._k(key), value, px=int(ttl * 1000))
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
                pipe.set(self._k(k), v, px=int(ttl * 1000))
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
        import hashlib

        key = hashlib.sha256(value).hexdigest()
        await self.write(key, value, ttl_seconds=ttl_seconds)
        return key

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
                        pipe.set(rk, new, px=int(ttl * 1000))
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
