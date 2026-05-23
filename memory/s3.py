"""S3Store: blob / cold-storage MemoryStore adapter (BL-032, ADR 0007).

``boto3`` is an optional dependency, imported lazily. Install:
``pip install 'agents[aws]'``.

Semantics deviation (documented per ADR 0004):

- S3 has no native per-object TTL (only coarse, bucket-level lifecycle
  rules). Expiry is stored in object metadata (``x-amz-meta-expires-at``)
  and enforced lazily on access and by ``sweep_expired`` -- the same
  lazy model as InMemoryStore, not server-side eviction.
- S3 is read-after-write consistent for new keys but overwrite/delete
  visibility and LIST can briefly lag. Callers needing strict
  monotonicity should use Redis/SQLite/DynamoDB instead. This is the
  cold-storage / audit-pack backend.
- No CAS (S3 has no atomic compare-and-set on object content), so
  CASMemoryStore is intentionally not implemented.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from memory._audit import MemoryAudit
from memory._expiry import is_expired
from memory.types import Namespace
from memory.validators import validate_key

__all__ = ["S3Store"]

_EXPIRES_META = "expires-at"


class S3Store:
    """Cold-storage MemoryStore backed by an S3 bucket."""

    name: str = "s3"

    def __init__(
        self,
        namespace: Namespace,
        bucket: str,
        *,
        client: Any | None = None,
        prefix: str | None = None,
        sink: Any | None = None,
        base_event_fields: dict[str, Any] | None = None,
    ) -> None:
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - env dependent
                raise ImportError(
                    "S3Store requires the 'aws' extra: pip install 'agents[aws]'"
                ) from exc
            client = boto3.client("s3")
        self._s3 = client
        self._bucket = bucket
        self._namespace = namespace
        self._prefix = f"{prefix or namespace.name}/"
        self._audit = MemoryAudit(namespace.name, sink, base_event_fields)

    @property
    def namespace(self) -> Namespace:
        return self._namespace

    def _okey(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def _ttl(self, ttl_seconds: float | None) -> float | None:
        return ttl_seconds if ttl_seconds is not None else self._namespace.retention_seconds

    def _get_live(self, key: str) -> bytes | None:
        """Return value if present and unexpired; delete it if expired.

        Only an object-level not-found is a miss. Other ClientErrors
        (AccessDenied, throttling, transient outages, and
        ``NoSuchBucket`` -- a misconfigured/deleted bucket is a backend
        failure, not an absent key) propagate so an outage is not
        silently reported as "key absent".
        """
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=self._okey(key))
        except self._s3.exceptions.NoSuchKey:
            return None
        except self._s3.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404", "NotFound"):
                return None
            raise
        exp = obj.get("Metadata", {}).get(_EXPIRES_META)
        if is_expired(time.time(), float(exp) if exp is not None else None):
            self._s3.delete_object(Bucket=self._bucket, Key=self._okey(key))
            return None
        body = obj["Body"].read()
        return bytes(body)

    # boto3 is synchronous; every blocking call is offloaded to a worker
    # thread so an asyncio workload's event loop is never stalled by S3
    # network I/O. Audit emission stays on the loop (fast, in-memory).

    async def read(self, key: str) -> bytes | None:
        validate_key(key)
        value = await asyncio.to_thread(self._get_live, key)
        self._audit.read(key, hit=value is not None)
        return value

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        validate_key(key)
        ttl = self._ttl(ttl_seconds)
        metadata: dict[str, str] = {}
        if ttl is not None:
            metadata[_EXPIRES_META] = str(time.time() + ttl)
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._bucket,
            Key=self._okey(key),
            Body=value,
            Metadata=metadata,
        )
        self._audit.write(key, value_bytes=len(value), ttl_seconds=ttl)

    def _delete_sync(self, key: str) -> bool:
        existed = self._get_live(key) is not None
        self._s3.delete_object(Bucket=self._bucket, Key=self._okey(key))
        return existed

    async def delete(self, key: str) -> None:
        validate_key(key)
        existed = await asyncio.to_thread(self._delete_sync, key)
        self._audit.delete(key, existed=existed)

    def _all_live_keys(self, prefix: str = "") -> list[str]:
        # Push ``prefix`` into the S3 LIST (server-side), so listing a
        # subtree no longer pulls the whole namespace and HEAD/GETs every
        # object just to discard most of them (BL-161).
        full_prefix = f"{self._prefix}{prefix}"
        keys: list[str] = []
        token: str | None = None
        while True:
            kw: dict[str, Any] = {"Bucket": self._bucket, "Prefix": full_prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kw)
            for item in resp.get("Contents", []):
                short = item["Key"][len(self._prefix) :]
                if self._get_live(short) is not None:
                    keys.append(short)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return sorted(keys)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._all_live_keys, prefix)

    async def mget(self, keys: list[str]) -> list[bytes | None]:
        for k in keys:
            validate_key(k)
        out: list[bytes | None] = await asyncio.to_thread(lambda: [self._get_live(k) for k in keys])
        for k, v in zip(keys, out, strict=True):
            self._audit.read(k, hit=v is not None)
        return out

    async def mset(self, items: dict[str, bytes], *, ttl_seconds: float | None = None) -> None:
        for k, v in items.items():
            await self.write(k, v, ttl_seconds=ttl_seconds)

    async def mdelete(self, keys: list[str]) -> None:
        for k in keys:
            await self.delete(k)

    def _scan_sync(self, cursor: str, prefix: str, count: int) -> tuple[str, list[str]]:
        # Exclude expired-but-unswept keys so scan() agrees with
        # read()/list_keys(). A raw S3 page can be entirely expired keys
        # while more live keys exist behind a continuation token;
        # returning ("", []) there would wrongly signal exhaustion
        # (audit B1). Page internally until at least one live key is
        # collected or the listing truly ends, then return the cursor.
        keys: list[str] = []
        token = cursor
        next_cursor = ""
        while True:
            kw: dict[str, Any] = {
                "Bucket": self._bucket,
                "Prefix": f"{self._prefix}{prefix}",
                "MaxKeys": count,
            }
            if token:
                kw["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kw)
            keys.extend(
                short
                for item in resp.get("Contents", [])
                if self._get_live(short := item["Key"][len(self._prefix) :]) is not None
            )
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken", "")
                next_cursor = token
            else:
                next_cursor = ""
            if keys or not next_cursor:
                break
        return next_cursor, sorted(keys)

    async def scan(
        self, *, cursor: str = "", prefix: str = "", count: int = 100
    ) -> tuple[str, list[str]]:
        if count <= 0:
            return "", []
        return await asyncio.to_thread(self._scan_sync, cursor, prefix, count)

    async def write_content(self, value: bytes, *, ttl_seconds: float | None = None) -> str:
        key = hashlib.sha256(value).hexdigest()
        await self.write(key, value, ttl_seconds=ttl_seconds)
        return key

    def _sweep_sync(self) -> int:
        removed = 0
        token: str | None = None
        while True:
            kw: dict[str, Any] = {"Bucket": self._bucket, "Prefix": self._prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kw)
            for item in resp.get("Contents", []):
                head = self._s3.head_object(Bucket=self._bucket, Key=item["Key"])
                exp = head.get("Metadata", {}).get(_EXPIRES_META)
                if is_expired(time.time(), float(exp) if exp is not None else None):
                    self._s3.delete_object(Bucket=self._bucket, Key=item["Key"])
                    removed += 1
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return removed

    async def sweep_expired(self) -> int:
        return await asyncio.to_thread(self._sweep_sync)
