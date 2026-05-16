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

import hashlib
import time
from typing import Any

from memory._audit import MemoryAudit
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
        """Return value if present and unexpired; delete it if expired."""
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=self._okey(key))
        except self._s3.exceptions.NoSuchKey:
            return None
        except self._s3.exceptions.ClientError:
            return None
        exp = obj.get("Metadata", {}).get(_EXPIRES_META)
        if exp is not None and time.time() > float(exp):
            self._s3.delete_object(Bucket=self._bucket, Key=self._okey(key))
            return None
        body = obj["Body"].read()
        return bytes(body)

    async def read(self, key: str) -> bytes | None:
        validate_key(key)
        value = self._get_live(key)
        self._audit.read(key, hit=value is not None)
        return value

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        validate_key(key)
        ttl = self._ttl(ttl_seconds)
        metadata: dict[str, str] = {}
        if ttl is not None:
            metadata[_EXPIRES_META] = str(time.time() + ttl)
        self._s3.put_object(Bucket=self._bucket, Key=self._okey(key), Body=value, Metadata=metadata)
        self._audit.write(key, value_bytes=len(value), ttl_seconds=ttl)

    async def delete(self, key: str) -> None:
        validate_key(key)
        existed = self._get_live(key) is not None
        self._s3.delete_object(Bucket=self._bucket, Key=self._okey(key))
        self._audit.delete(key, existed=existed)

    def _all_live_keys(self) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            kw: dict[str, Any] = {"Bucket": self._bucket, "Prefix": self._prefix}
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
        return [k for k in self._all_live_keys() if k.startswith(prefix)]

    async def mget(self, keys: list[str]) -> list[bytes | None]:
        for k in keys:
            validate_key(k)
        out: list[bytes | None] = []
        for k in keys:
            v = self._get_live(k)
            self._audit.read(k, hit=v is not None)
            out.append(v)
        return out

    async def mset(self, items: dict[str, bytes], *, ttl_seconds: float | None = None) -> None:
        for k, v in items.items():
            await self.write(k, v, ttl_seconds=ttl_seconds)

    async def mdelete(self, keys: list[str]) -> None:
        for k in keys:
            await self.delete(k)

    async def scan(
        self, *, cursor: str = "", prefix: str = "", count: int = 100
    ) -> tuple[str, list[str]]:
        if count <= 0:
            return "", []
        kw: dict[str, Any] = {
            "Bucket": self._bucket,
            "Prefix": f"{self._prefix}{prefix}",
            "MaxKeys": count,
        }
        if cursor:
            kw["ContinuationToken"] = cursor
        resp = self._s3.list_objects_v2(**kw)
        # Exclude expired-but-unswept keys so scan() agrees with
        # read()/list_keys() and the ScannableStore contract (a page may
        # then yield fewer than `count`; the caller continues via the
        # cursor). The head-per-key cost is the documented S3 tradeoff.
        keys = sorted(
            short
            for item in resp.get("Contents", [])
            if self._get_live(short := item["Key"][len(self._prefix) :]) is not None
        )
        next_cursor = resp.get("NextContinuationToken", "") if resp.get("IsTruncated") else ""
        return next_cursor, keys

    async def write_content(self, value: bytes, *, ttl_seconds: float | None = None) -> str:
        key = hashlib.sha256(value).hexdigest()
        await self.write(key, value, ttl_seconds=ttl_seconds)
        return key

    async def sweep_expired(self) -> int:
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
                if exp is not None and time.time() > float(exp):
                    self._s3.delete_object(Bucket=self._bucket, Key=item["Key"])
                    removed += 1
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return removed
