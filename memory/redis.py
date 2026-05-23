"""RedisStore: production MemoryStore adapter (BL-030, ADR 0007).

``redis`` is an optional dependency: this module imports it lazily so
the core package works without it. Install the extra:
``pip install 'agents[redis]'``.

Design:

- Native TTL via ``SET ... PX`` (millisecond precision). No lazy-expiry
  bookkeeping and no SweepableStore on the bare ``RedisStore``: Redis
  evicts expired keys itself. ``BoundedRedisStore`` (`BL-214`) is the
  opt-in subclass that maintains a per-namespace insertion-order
  sorted-set index and implements ``SweepableStore`` +
  ``BoundedSweepableStore`` over it.
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

__all__ = ["BoundedRedisStore", "RedisStore"]

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
        # Delegate to Namespace.resolve_ttl (BL-197).
        return self._namespace.resolve_ttl(ttl_seconds)

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
        if not items:
            # Empty-batch short-circuit (BL-198, BL-178 class extension):
            # parity with mdelete and with the BL-178 fix in SQLiteStore.
            # An empty pipeline.execute() round-trips for no work.
            return
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


class BoundedRedisStore(RedisStore):
    """RedisStore + per-namespace insertion-order sorted-set index (`BL-214`).

    The Redis counterpart to `BL-213`'s SQLite reference, parallel to
    how `BL-180` extended `BL-124` to the network-durable adapters.
    Maintains two auxiliary keys per namespace, both placed OUTSIDE
    the namespace prefix so they cannot collide with a user key and
    do not appear in ``list_keys`` / ``scan`` results:

    - ``__evict_index::<namespace>``: a sorted set whose *members*
      are user keys and whose *scores* are server-side monotonic
      sequence numbers from the counter below. ZRANGE ascending by
      score yields oldest-first eviction order.
    - ``__evict_counter::<namespace>``: an integer counter advanced
      via INCR / INCRBY. Each ZADD score comes from this counter, so
      ordering is server-defined (not client-wall-clock) and unique
      per write. Multi-writer deployments stay correct under clock
      skew (the counter is single-threaded inside Redis), and
      sub-second back-to-back writes do not collide on a single
      score (so Redis cannot tie-break ZADD members by name and
      break the documented insertion-order semantic).

    Every keyspace-mutating method also updates the index, so
    ``evict_to_capacity`` can walk oldest-first by index score
    without scanning the keyspace.

    Cost model: every write costs two extra Redis round trips
    (the INCR / INCRBY for the score, then the ZADD with that
    score). For high-throughput workloads, prefer ``mset`` /
    ``transact``, which pay the two extra round trips per batch
    (one INCRBY for the whole batch, one ZADD with multiple
    member-score pairs) not per item. Use ``RedisStore`` directly
    when the size-bound is not needed; the bare class has no
    auxiliary index, no counter, and no per-write overhead. This is
    the same opt-in trade-off pattern as ``EncryptedStore`` (BL-070)
    over a plain backend: pay for the capability when you want it.

    Eviction order: ZRANGE ascending by score, which is INCR-order
    ascending (server-side monotonic, robust against client clock
    skew). A re-write of an existing key allocates a new score and
    ZADD updates the index entry's score, so the rewritten key
    orders as *newest* by index, matching the SQLite
    ``INSERT OR REPLACE`` semantic (`BL-213`) and diverging from
    the InMemoryStore first-write FIFO (`BL-212`). Within a single
    ``mset`` / ``transact`` batch, scores are assigned in dict
    iteration order (Python 3.7+ preserves insertion order), so the
    caller's intended FIFO order is preserved.

    Sweep responsibility: Redis auto-evicts a TTL'd data key on its
    own schedule, but the sorted-set entry for that user key
    persists until cleaned. ``sweep_expired`` walks the index,
    issues a pipelined EXISTS for each member, and ZREMs every
    member whose underlying data key is gone. The size-bound
    capacity pass (``evict_to_capacity``) performs the same
    staleness filter so a member whose underlying key has already
    expired is not counted against the cap.
    """

    name: str = "redis-bounded"

    @property
    def _idx(self) -> str:
        # Auxiliary sorted-set index key, placed OUTSIDE the namespace
        # prefix (``__evict_index::<namespace>``) so:
        #   1. It can never collide with a user key. Every user-written
        #      Redis key starts with ``<namespace>::`` (the namespace
        #      prefix), but the index key starts with ``__evict_index``,
        #      a prefix no namespace can have because
        #      ``validate_namespace_name`` requires
        #      ``^[a-z0-9][a-z0-9_-]{0,63}$`` (lowercase letter or
        #      digit at index 0, no leading underscore). The
        #      lowercase leading underscore on ``__evict_index`` here
        #      is exactly the shape the validator forbids in a
        #      namespace, so the colliding-namespace case is
        #      structurally impossible.
        #   2. It does not appear in ``list_keys`` or ``scan`` results.
        #      Those filter by ``<namespace>::<prefix>*``, which the
        #      index key (no namespace prefix) cannot match. No filter
        #      override needed.
        return f"__evict_index::{self._namespace.name}"

    @property
    def _counter(self) -> str:
        # Auxiliary monotonic counter key, placed under the same
        # `__evict_*::<namespace>` collision-safe convention as
        # ``_idx``. Used to allocate unique server-side scores via
        # INCR / INCRBY so ordering is robust against client clock
        # skew (BL-214 review: Copilot + Codex P1/P2 on the score
        # source).
        return f"__evict_counter::{self._namespace.name}"

    async def _next_score(self) -> int:
        """Allocate one monotonic score from the namespace counter."""
        return int(await self._r.incr(self._counter))

    async def _next_scores(self, count: int) -> list[int]:
        """Allocate ``count`` contiguous monotonic scores in one round
        trip via INCRBY, returned in allocation order so the caller can
        assign them to members in their intended FIFO order. ``count``
        must be positive; ``count == 0`` returns ``[]`` without a
        round trip.
        """
        if count <= 0:
            return []
        end = int(await self._r.incrby(self._counter, count))
        return list(range(end - count + 1, end + 1))

    # --- mutating methods: maintain the index alongside the parent op -

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        await super().write(key, value, ttl_seconds=ttl_seconds)
        score = await self._next_score()
        await self._r.zadd(self._idx, {key: score})

    async def mset(self, items: dict[str, bytes], *, ttl_seconds: float | None = None) -> None:
        await super().mset(items, ttl_seconds=ttl_seconds)
        if items:
            # Allocate one score per member from a single INCRBY, then
            # zip them to keys in dict iteration order. Python 3.7+
            # preserves dict insertion order so the FIFO contract holds
            # within the batch (Codex PR #60 P2 on batch tie-breaks).
            scores = await self._next_scores(len(items))
            await self._r.zadd(self._idx, dict(zip(items.keys(), scores, strict=True)))

    async def delete(self, key: str) -> None:
        await super().delete(key)
        await self._r.zrem(self._idx, key)

    async def mdelete(self, keys: list[str]) -> None:
        await super().mdelete(keys)
        if keys:
            await self._r.zrem(self._idx, *keys)

    async def compare_and_set(
        self,
        key: str,
        expected: bytes | None,
        new: bytes,
        *,
        ttl_seconds: float | None = None,
    ) -> bool:
        ok = await super().compare_and_set(key, expected, new, ttl_seconds=ttl_seconds)
        if ok:
            # The CAS succeeded so the key is freshly set; allocate a
            # new monotonic score so a CAS-updated key sorts as newest
            # by eviction order, matching the BL-213
            # overwrite-shifts-to-newest semantic.
            score = await self._next_score()
            await self._r.zadd(self._idx, {key: score})
        return ok

    async def compare_and_delete(self, key: str, expected: bytes) -> bool:
        ok = await super().compare_and_delete(key, expected)
        if ok:
            await self._r.zrem(self._idx, key)
        return ok

    async def write_versioned(
        self,
        key: str,
        value: bytes,
        *,
        expected_version: str | None = None,
        ttl_seconds: float | None = None,
    ) -> str | None:
        token = await super().write_versioned(
            key, value, expected_version=expected_version, ttl_seconds=ttl_seconds
        )
        if token is not None:
            score = await self._next_score()
            await self._r.zadd(self._idx, {key: score})
        return token

    async def delete_versioned(self, key: str, expected_version: str) -> bool:
        ok = await super().delete_versioned(key, expected_version)
        if ok:
            await self._r.zrem(self._idx, key)
        return ok

    async def transact(
        self,
        *,
        writes: Mapping[str, TxnWrite] | None = None,
        deletes: Mapping[str, TxnDelete] | None = None,
    ) -> dict[str, str] | None:
        out = await super().transact(writes=writes, deletes=deletes)
        if out is None:
            # Precondition failure or persistent contention; the
            # parent emitted no audit and made no keyspace change, so
            # the index is unchanged.
            return out
        # Allocate writes-many scores in a single INCRBY before the
        # pipelined ZADD/ZREM, so write-order ZADD scores are unique
        # and monotonic in dict iteration order (Python 3.7+
        # preserves insertion order). One extra INCRBY round trip
        # per transact() regardless of batch size.
        scores: list[int] = []
        if writes:
            scores = await self._next_scores(len(writes))
        pipe = self._r.pipeline(transaction=False)
        if writes:
            pipe.zadd(self._idx, dict(zip(writes.keys(), scores, strict=True)))
        if deletes:
            pipe.zrem(self._idx, *deletes)
        await pipe.execute()
        return out

    # --- SweepableStore (BL-080) --------------------------------------

    async def sweep_expired(self) -> int:
        """Clean stale index members whose underlying Redis keys have
        already been evicted by Redis. Returns the count cleaned (not
        the count of data keys evicted: Redis evicted them on its own
        schedule; this method only catches up the auxiliary index).
        """
        members = await self._members()
        if not members:
            return 0
        live_map = await self._live_map(members)
        stale = [m for m, alive in live_map.items() if not alive]
        if stale:
            await self._r.zrem(self._idx, *stale)
        return len(stale)

    # --- BoundedSweepableStore (BL-214, BL-135 size-bound on Redis) ---

    async def evict_to_capacity(self, max_keys: int) -> int:
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
        members = await self._members()
        if not members:
            return 0
        live_map = await self._live_map(members)
        live = [m for m in members if live_map[m]]
        stale = [m for m in members if not live_map[m]]
        # Clean stale index entries first so the next call sees only
        # live members. This is the BL-195 read-vs-listing parity in
        # Redis form: a member whose underlying key has expired must
        # not be counted toward the cap.
        if stale:
            await self._r.zrem(self._idx, *stale)
        overflow = len(live) - max_keys
        if overflow <= 0:
            return 0
        # ZRANGE returned ascending by score, so live[:overflow] is the
        # oldest live block.
        to_evict = live[:overflow]
        pipe = self._r.pipeline(transaction=False)
        for k in to_evict:
            pipe.delete(self._k(k))
        pipe.zrem(self._idx, *to_evict)
        await pipe.execute()
        for k in to_evict:
            self._audit.delete(k, existed=True)
        return len(to_evict)

    # --- helpers ------------------------------------------------------

    async def _members(self) -> list[str]:
        raw = await self._r.zrange(self._idx, 0, -1)
        return [m.decode() if isinstance(m, bytes) else m for m in raw]

    async def _live_map(self, members: list[str]) -> dict[str, bool]:
        """Pipelined EXISTS check per member; returns a dict insertion-
        ordered the same as ``members`` so caller can preserve the
        ZRANGE order when filtering.
        """
        pipe = self._r.pipeline(transaction=False)
        for m in members:
            pipe.exists(self._k(m))
        results = await pipe.execute()
        return {m: bool(e) for m, e in zip(members, results, strict=True)}
