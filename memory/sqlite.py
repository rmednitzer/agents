"""SQLiteStore: durable single-host MemoryStore adapter (BL-031, ADR 0007).

Backed by the stdlib ``sqlite3`` (always available, so always tested).
One connection per store, WAL journal mode for concurrent readers, one
table per namespace in the database file. Blocking DB calls run in a
worker thread (``asyncio.to_thread``) and are serialized by an
asyncio.Lock so the single connection is never touched concurrently.

Implements MemoryStore plus every extension Protocol: Batch, Scan,
ContentAddressable, CAS (via ``BEGIN IMMEDIATE`` transactions), and
Sweepable. Expiry is lazy on access (rows are deleted when found
expired); ``sweep_expired`` / TTLSweeper reclaim space proactively.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from harness.sinks import EventSink
from memory._audit import MemoryAudit
from memory.types import Namespace
from memory.validators import validate_key

__all__ = ["SQLiteStore"]


def _table_for(namespace: str) -> str:
    """Per-namespace table name, preserving the namespace verbatim.

    The namespace charset is regex-constrained to ``[a-z0-9_-]`` (no
    quotes), so interpolation is injection-safe, and every query
    double-quotes the identifier -- so a hyphen is legal. The name is
    NOT transformed: mapping '-' -> '_' would collapse distinct
    namespaces ('a-b' and 'a_b') onto one table and break the structural
    isolation guarantee (ADR 0004).
    """
    return f"kv_{namespace}"


class SQLiteStore:
    """Durable single-host MemoryStore backed by sqlite3."""

    name: str = "sqlite"

    def __init__(
        self,
        namespace: Namespace,
        database: str | Path = ":memory:",
        *,
        sink: EventSink | None = None,
        base_event_fields: dict[str, Any] | None = None,
    ) -> None:
        self._namespace = namespace
        self._table = _table_for(namespace.name)
        self._audit = MemoryAudit(namespace.name, sink, base_event_fields)
        self._lock = asyncio.Lock()
        self._conn = sqlite3.connect(str(database), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{self._table}" '
            "(key TEXT PRIMARY KEY, value BLOB NOT NULL, expires_at REAL)"
        )

    @property
    def namespace(self) -> Namespace:
        return self._namespace

    def close(self) -> None:
        self._conn.close()

    def _effective_ttl(self, ttl_seconds: float | None) -> float | None:
        return ttl_seconds if ttl_seconds is not None else self._namespace.retention_seconds

    # --- sync DB primitives (run via asyncio.to_thread under the lock) --

    def _db_get(self, key: str) -> bytes | None:
        cur = self._conn.execute(
            f'SELECT value, expires_at FROM "{self._table}" WHERE key=?', (key,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        value, expires_at = row
        if expires_at is not None and time.time() > expires_at:
            self._conn.execute(f'DELETE FROM "{self._table}" WHERE key=?', (key,))
            return None
        return bytes(value)

    def _db_put(self, key: str, value: bytes, expires_at: float | None) -> None:
        self._conn.execute(
            f'INSERT OR REPLACE INTO "{self._table}" (key, value, expires_at) VALUES (?, ?, ?)',
            (key, value, expires_at),
        )

    def _db_live_keys(self) -> list[tuple[str, float | None]]:
        cur = self._conn.execute(f'SELECT key, expires_at FROM "{self._table}"')
        return cur.fetchall()

    # --- core MemoryStore ---------------------------------------------

    async def read(self, key: str) -> bytes | None:
        validate_key(key)
        async with self._lock:
            value = await asyncio.to_thread(self._db_get, key)
        self._audit.read(key, hit=value is not None)
        return value

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        validate_key(key)
        ttl = self._effective_ttl(ttl_seconds)
        expires_at = time.time() + ttl if ttl is not None else None
        async with self._lock:
            await asyncio.to_thread(self._db_put, key, value, expires_at)
        self._audit.write(key, value_bytes=len(value), ttl_seconds=ttl)

    async def delete(self, key: str) -> None:
        validate_key(key)
        async with self._lock:
            existed = await asyncio.to_thread(self._db_get, key) is not None
            await asyncio.to_thread(
                self._conn.execute,
                f'DELETE FROM "{self._table}" WHERE key=?',
                (key,),
            )
        self._audit.delete(key, existed=existed)

    async def list_keys(self, prefix: str = "") -> list[str]:
        async with self._lock:
            rows = await asyncio.to_thread(self._db_live_keys)
        now = time.time()
        return sorted(k for k, exp in rows if (exp is None or now <= exp) and k.startswith(prefix))

    # --- BatchMemoryStore (BL-081) ------------------------------------

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        for k in keys:
            validate_key(k)
        async with self._lock:
            values = [await asyncio.to_thread(self._db_get, k) for k in keys]
        for k, v in zip(keys, values, strict=True):
            self._audit.read(k, hit=v is not None)
        return values

    async def mset(self, items: Mapping[str, bytes], *, ttl_seconds: float | None = None) -> None:
        for k in items:
            validate_key(k)
        if not items:
            # An empty batch is a no-op; opening BEGIN IMMEDIATE would
            # take the database write lock to do nothing (needless
            # contention against concurrent writers).
            return
        ttl = self._effective_ttl(ttl_seconds)
        expires_at = time.time() + ttl if ttl is not None else None

        def _bulk() -> None:
            # One BEGIN IMMEDIATE transaction so a multi-key set is
            # atomic (all rows or none), matching the BatchMemoryStore
            # all-or-nothing contract and the CAS path (BL-161).
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.executemany(
                    f'INSERT OR REPLACE INTO "{self._table}" '
                    "(key, value, expires_at) VALUES (?, ?, ?)",
                    [(k, v, expires_at) for k, v in items.items()],
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        async with self._lock:
            await asyncio.to_thread(_bulk)
        for k, v in items.items():
            self._audit.write(k, value_bytes=len(v), ttl_seconds=ttl)

    async def mdelete(self, keys: Sequence[str]) -> None:
        for k in keys:
            validate_key(k)
        if not keys:
            return

        def _bulk_delete() -> dict[str, bool]:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existed = {k: self._db_get(k) is not None for k in keys}
                self._conn.executemany(
                    f'DELETE FROM "{self._table}" WHERE key=?',
                    [(k,) for k in keys],
                )
                self._conn.execute("COMMIT")
                return existed
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        async with self._lock:
            existed = await asyncio.to_thread(_bulk_delete)
        for k, did in existed.items():
            self._audit.delete(k, existed=did)

    # --- ScannableStore (BL-082) --------------------------------------

    async def scan(
        self, *, cursor: str = "", prefix: str = "", count: int = 100
    ) -> tuple[str, list[str]]:
        if count <= 0:
            return "", []
        async with self._lock:
            rows = await asyncio.to_thread(self._db_live_keys)
        now = time.time()
        candidates = sorted(
            k
            for k, exp in rows
            if (exp is None or now <= exp) and k.startswith(prefix) and (cursor == "" or k > cursor)
        )
        page = candidates[:count]
        next_cursor = page[-1] if len(candidates) > count else ""
        return next_cursor, page

    # --- ContentAddressableStore (BL-083) -----------------------------

    async def write_content(self, value: bytes, *, ttl_seconds: float | None = None) -> str:
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
        ttl = self._effective_ttl(ttl_seconds)
        expires_at = time.time() + ttl if ttl is not None else None

        def _cas() -> bool:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._db_get(key)
                if current != expected:
                    self._conn.execute("ROLLBACK")
                    return False
                self._db_put(key, new, expires_at)
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        async with self._lock:
            ok = await asyncio.to_thread(_cas)
        if ok:
            self._audit.write(key, value_bytes=len(new), ttl_seconds=ttl)
        return ok

    async def compare_and_delete(self, key: str, expected: bytes) -> bool:
        validate_key(key)

        def _cad() -> bool:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._db_get(key)
                if current != expected:
                    self._conn.execute("ROLLBACK")
                    return False
                self._conn.execute(f'DELETE FROM "{self._table}" WHERE key=?', (key,))
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        async with self._lock:
            ok = await asyncio.to_thread(_cad)
        if ok:
            self._audit.delete(key, existed=True)
        return ok

    # --- VersionedMemoryStore (BL-124) --------------------------------

    @staticmethod
    def _token(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    async def read_versioned(self, key: str) -> tuple[bytes, str] | None:
        validate_key(key)
        async with self._lock:
            value = await asyncio.to_thread(self._db_get, key)
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
        validate_key(key)
        ttl = self._effective_ttl(ttl_seconds)
        expires_at = time.time() + ttl if ttl is not None else None

        def _vset() -> bool:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._db_get(key)
                live = None if current is None else self._token(current)
                if live != expected_version:
                    self._conn.execute("ROLLBACK")
                    return False
                self._db_put(key, value, expires_at)
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        async with self._lock:
            ok = await asyncio.to_thread(_vset)
        if not ok:
            return None
        self._audit.write(key, value_bytes=len(value), ttl_seconds=ttl)
        return self._token(value)

    async def delete_versioned(self, key: str, expected_version: str) -> bool:
        validate_key(key)

        def _vdel() -> bool:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._db_get(key)
                if current is None or self._token(current) != expected_version:
                    self._conn.execute("ROLLBACK")
                    return False
                self._conn.execute(f'DELETE FROM "{self._table}" WHERE key=?', (key,))
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        async with self._lock:
            ok = await asyncio.to_thread(_vdel)
        if ok:
            self._audit.delete(key, existed=True)
        return ok

    # --- SweepableStore (BL-080) --------------------------------------

    async def sweep_expired(self) -> int:
        def _sweep() -> int:
            # Strict ``<`` matches read()/list_keys()/scan() which treat
            # an entry live until ``now > expires_at`` (live at the exact
            # expiry instant). ``<=`` here swept an entry the readers
            # still considered live at that instant (audit A6: read vs
            # sweep boundary; list_keys/scan aligned to the same
            # ``now <= exp`` live boundary in the same class fix).
            cur = self._conn.execute(
                f'DELETE FROM "{self._table}" WHERE expires_at IS NOT NULL AND expires_at < ?',
                (time.time(),),
            )
            return cur.rowcount

        async with self._lock:
            return await asyncio.to_thread(_sweep)
