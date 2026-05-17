"""DynamoDBStore: AWS-native MemoryStore adapter (BL-033, ADR 0007).

``boto3`` is an optional dependency, imported lazily. Install:
``pip install 'agents[aws]'``.

Design:

- One table, partition key ``pk = "<namespace>::<key>"`` so many
  namespaces share a table while staying isolated (``validate_key``
  forbids ``"::"`` in user keys).
- ``exp`` numeric attribute is the DynamoDB TTL attribute. DynamoDB's
  own TTL sweep is best-effort and lags up to ~48 h, so expiry is also
  enforced lazily on read (and by ``sweep_expired``).
- ``consistent_read`` (default False) selects eventually- vs
  strongly-consistent reads, the documented optional knob.
- CAS maps onto DynamoDB conditional expressions (atomic server-side).
- ``scan`` is cursor-paged; the opaque cursor is the base64 of
  LastEvaluatedKey, which callers must not parse.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from typing import Any

from memory._audit import MemoryAudit
from memory.errors import MemoryError as _MemoryError
from memory.types import Namespace
from memory.validators import validate_key

__all__ = ["DynamoDBStore"]

_BATCH_MAX_RETRIES = 8


class DynamoDBStore:
    """AWS-native MemoryStore backed by a DynamoDB table."""

    name: str = "dynamodb"

    def __init__(
        self,
        namespace: Namespace,
        table: str,
        *,
        client: Any | None = None,
        consistent_read: bool = False,
        sink: Any | None = None,
        base_event_fields: dict[str, Any] | None = None,
    ) -> None:
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - env dependent
                raise ImportError(
                    "DynamoDBStore requires the 'aws' extra: pip install 'agents[aws]'"
                ) from exc
            client = boto3.client("dynamodb")
        self._db = client
        self._table = table
        self._namespace = namespace
        self._pfx = f"{namespace.name}::"
        self._consistent = consistent_read
        self._audit = MemoryAudit(namespace.name, sink, base_event_fields)

    @property
    def namespace(self) -> Namespace:
        return self._namespace

    def _pk(self, key: str) -> str:
        return f"{self._pfx}{key}"

    def _ttl(self, ttl_seconds: float | None) -> float | None:
        return ttl_seconds if ttl_seconds is not None else self._namespace.retention_seconds

    def _live_item(self, key: str) -> Any:
        resp = self._db.get_item(
            TableName=self._table,
            Key={"pk": {"S": self._pk(key)}},
            ConsistentRead=self._consistent,
        )
        item = resp.get("Item")
        if item is None:
            return None
        exp = item.get("exp", {}).get("N")
        if exp is not None and time.time() > float(exp):
            self._db.delete_item(TableName=self._table, Key={"pk": {"S": self._pk(key)}})
            return None
        return item

    # boto3 is synchronous; blocking calls run in a worker thread so an
    # asyncio event loop is never stalled by DynamoDB network I/O.

    async def read(self, key: str) -> bytes | None:
        validate_key(key)
        item = await asyncio.to_thread(self._live_item, key)
        value = bytes(item["v"]["B"]) if item is not None else None
        self._audit.read(key, hit=value is not None)
        return value

    def _item(self, key: str, value: bytes, ttl: float | None) -> dict[str, Any]:
        item: dict[str, Any] = {"pk": {"S": self._pk(key)}, "v": {"B": value}}
        if ttl is not None:
            # Float seconds (BL-157): matches InMemory/SQLite/S3 and the
            # ``float(exp)`` read path, so a sub-second TTL is honoured
            # rather than truncated to the next integer second.
            # DynamoDB's own native TTL sweep reads the integer part and
            # is best-effort/lagging anyway (documented), so the
            # fractional part only sharpens the lazy-expiry boundary.
            item["exp"] = {"N": str(time.time() + ttl)}
        return item

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        validate_key(key)
        ttl = self._ttl(ttl_seconds)
        await asyncio.to_thread(
            self._db.put_item, TableName=self._table, Item=self._item(key, value, ttl)
        )
        self._audit.write(key, value_bytes=len(value), ttl_seconds=ttl)

    def _delete_sync(self, key: str) -> bool:
        existed = self._live_item(key) is not None
        self._db.delete_item(TableName=self._table, Key={"pk": {"S": self._pk(key)}})
        return existed

    async def delete(self, key: str) -> None:
        validate_key(key)
        existed = await asyncio.to_thread(self._delete_sync, key)
        self._audit.delete(key, existed=existed)

    def _scan_page(self, start: dict[str, Any] | None, limit: int | None) -> Any:
        kw: dict[str, Any] = {
            "TableName": self._table,
            "FilterExpression": "begins_with(pk, :p)",
            "ExpressionAttributeValues": {":p": {"S": self._pfx}},
        }
        if start is not None:
            kw["ExclusiveStartKey"] = start
        if limit is not None:
            kw["Limit"] = limit
        return self._db.scan(**kw)

    def _list_sync(self, prefix: str) -> list[str]:
        keys: list[str] = []
        start: dict[str, Any] | None = None
        now = time.time()
        while True:
            resp = self._scan_page(start, None)
            for item in resp.get("Items", []):
                exp = item.get("exp", {}).get("N")
                if exp is not None and now > float(exp):
                    continue
                short = item["pk"]["S"][len(self._pfx) :]
                if short.startswith(prefix):
                    keys.append(short)
            start = resp.get("LastEvaluatedKey")
            if not start:
                break
        return sorted(keys)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._list_sync, prefix)

    async def mget(self, keys: list[str]) -> list[bytes | None]:
        for k in keys:
            validate_key(k)

        def _mget_sync() -> list[bytes | None]:
            result: list[bytes | None] = []
            for k in keys:  # per-key keeps lazy-expiry semantics uniform
                item = self._live_item(k)
                result.append(bytes(item["v"]["B"]) if item is not None else None)
            return result

        out = await asyncio.to_thread(_mget_sync)
        for k, v in zip(keys, out, strict=True):
            self._audit.read(k, hit=v is not None)
        return out

    async def _batch_write(self, requests: list[dict[str, Any]]) -> None:
        """Submit write/delete requests in 25-item chunks.

        DynamoDB returns throttled requests in ``UnprocessedItems``
        rather than failing; ignoring them silently drops data. Each
        chunk is retried (bounded, exponential backoff) until empty; if
        items remain after the retry budget, raise so the caller does
        NOT emit success audit for dropped writes.
        """
        for i in range(0, len(requests), 25):
            pending = requests[i : i + 25]
            for attempt in range(_BATCH_MAX_RETRIES):
                resp = await asyncio.to_thread(
                    self._db.batch_write_item, RequestItems={self._table: pending}
                )
                pending = resp.get("UnprocessedItems", {}).get(self._table, [])
                if not pending:
                    break
                await asyncio.sleep(min(2**attempt * 0.05, 2.0))
            if pending:
                raise _MemoryError(
                    f"DynamoDB left {len(pending)} item(s) unprocessed after "
                    f"{_BATCH_MAX_RETRIES} retries"
                )

    async def mset(self, items: dict[str, bytes], *, ttl_seconds: float | None = None) -> None:
        for k in items:
            validate_key(k)
        ttl = self._ttl(ttl_seconds)
        await self._batch_write(
            [{"PutRequest": {"Item": self._item(k, v, ttl)}} for k, v in items.items()]
        )
        for k, v in items.items():
            self._audit.write(k, value_bytes=len(v), ttl_seconds=ttl)

    async def mdelete(self, keys: list[str]) -> None:
        for k in keys:
            validate_key(k)
        # The existence pre-check is sync boto3 I/O; offload it so a
        # large delete does not stall the event loop (parity with the
        # other paths in this adapter).
        existed = await asyncio.to_thread(lambda: {k: self._live_item(k) is not None for k in keys})
        await self._batch_write(
            [{"DeleteRequest": {"Key": {"pk": {"S": self._pk(k)}}}} for k in keys]
        )
        for k, did in existed.items():
            self._audit.delete(k, existed=did)

    def _scan_sync(self, cursor: str, prefix: str, count: int) -> tuple[str, list[str]]:
        start = json.loads(base64.b64decode(cursor).decode()) if cursor else None
        keys: list[str] = []
        next_cursor = ""
        while True:
            resp = self._scan_page(start, count)
            now = time.time()
            keys.extend(
                item["pk"]["S"][len(self._pfx) :]
                for item in resp.get("Items", [])
                if item["pk"]["S"][len(self._pfx) :].startswith(prefix)
                and not (item.get("exp", {}).get("N") is not None and now > float(item["exp"]["N"]))
            )
            lek = resp.get("LastEvaluatedKey")
            next_cursor = base64.b64encode(json.dumps(lek).encode()).decode() if lek else ""
            # DynamoDB Scan with a FilterExpression returns non-terminal
            # empty (or short) pages; ("", []) with keys still behind a
            # LastEvaluatedKey would falsely signal exhaustion (BL-161).
            # Page on until a live key is found or the scan truly ends.
            if keys or not next_cursor:
                break
            start = lek
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
        from botocore.exceptions import ClientError

        # Float ``:now`` (BL-157 / audit B6): the read path compares
        # ``time.time() > float(exp)``; an integer-truncated ``:now``
        # here disagreed with it for up to a second at the boundary.
        now = {"N": str(time.time())}
        kw: dict[str, Any] = {
            "TableName": self._table,
            "Item": self._item(key, new, ttl),
        }
        if expected is None:
            # Read treats an expired-but-unswept row as absent (Dynamo
            # TTL deletion lags); CAS-create must agree, so an expired
            # row also satisfies the "absent" precondition.
            kw["ConditionExpression"] = (
                "attribute_not_exists(pk) OR (attribute_exists(exp) AND exp < :now)"
            )
            kw["ExpressionAttributeValues"] = {":now": now}
        else:
            # Match value AND not expired (an expired row is absent).
            # ``exp >= :now`` (not ``>``) so the live boundary matches
            # the reader: ``_live_item`` treats a row as expired only
            # when ``now > float(exp)``, i.e. live while ``now <= exp``.
            # With strict ``>`` a row at the exact expiry instant was
            # readable but CAS-absent, the read-vs-CAS boundary class
            # BL-157/BL-168 fixed for the other paths/adapters.
            kw["ConditionExpression"] = "v = :e AND (attribute_not_exists(exp) OR exp >= :now)"
            kw["ExpressionAttributeValues"] = {":e": {"B": expected}, ":now": now}
        try:
            await asyncio.to_thread(lambda: self._db.put_item(**kw))
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        self._audit.write(key, value_bytes=len(new), ttl_seconds=ttl)
        return True

    async def compare_and_delete(self, key: str, expected: bytes) -> bool:
        validate_key(key)
        from botocore.exceptions import ClientError

        try:
            await asyncio.to_thread(
                lambda: self._db.delete_item(
                    TableName=self._table,
                    Key={"pk": {"S": self._pk(key)}},
                    # Parity with read(): an expired row is absent, so a
                    # compare-and-delete against it must not succeed.
                    # ``exp >= :now`` matches ``_live_item``'s live
                    # boundary (expired only when ``now > exp``), like
                    # the compare_and_set match-branch.
                    ConditionExpression=("v = :e AND (attribute_not_exists(exp) OR exp >= :now)"),
                    ExpressionAttributeValues={
                        ":e": {"B": expected},
                        ":now": {"N": str(time.time())},
                    },
                )
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        self._audit.delete(key, existed=True)
        return True

    def _sweep_sync(self) -> int:
        removed = 0
        start: dict[str, Any] | None = None
        now = time.time()
        while True:
            resp = self._scan_page(start, None)
            for item in resp.get("Items", []):
                exp = item.get("exp", {}).get("N")
                if exp is not None and now > float(exp):
                    self._db.delete_item(TableName=self._table, Key={"pk": item["pk"]})
                    removed += 1
            start = resp.get("LastEvaluatedKey")
            if not start:
                break
        return removed

    async def sweep_expired(self) -> int:
        return await asyncio.to_thread(self._sweep_sync)
