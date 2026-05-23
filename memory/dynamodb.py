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
- ``VersionedMemoryStore`` (BL-180) uses a server-stored ``ver``
  attribute (the content-hash of the value at write time) so the
  conditional expression is one round trip; every write path stamps
  ``ver`` so the attribute is always consistent with ``v``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from collections.abc import Mapping
from typing import Any

from memory._audit import MemoryAudit
from memory._expiry import is_expired, is_live
from memory.errors import MemoryError as _MemoryError
from memory.store import TxnDelete, TxnWrite
from memory.types import Namespace
from memory.validators import validate_key

__all__ = ["DynamoDBStore"]

_BATCH_MAX_RETRIES = 8
# DynamoDB TransactWriteItems hard limit (cf. DynamoDB Service Quotas).
_TRANSACT_MAX_ITEMS = 100


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
        # Delegate to Namespace.resolve_ttl (BL-197).
        return self._namespace.resolve_ttl(ttl_seconds)

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
        if is_expired(time.time(), float(exp) if exp is not None else None):
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

    @staticmethod
    def _token(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def _item(self, key: str, value: bytes, ttl: float | None) -> dict[str, Any]:
        # ``ver`` is the content-hash of ``value`` (BL-180); every write
        # path that builds an item via ``_item`` (write, mset,
        # compare_and_set) stamps it, so VersionedMemoryStore's
        # conditional expression can match in one round trip. Computing
        # the hash here keeps ``ver`` and ``v`` consistent by
        # construction.
        item: dict[str, Any] = {
            "pk": {"S": self._pk(key)},
            "v": {"B": value},
            "ver": {"S": self._token(value)},
        }
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
                if is_expired(now, float(exp) if exp is not None else None):
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
            for item in resp.get("Items", []):
                short = item["pk"]["S"][len(self._pfx) :]
                if not short.startswith(prefix):
                    continue
                exp = item.get("exp", {}).get("N")
                if is_live(now, float(exp) if exp is not None else None):
                    keys.append(short)
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

    # --- VersionedMemoryStore (BL-124, BL-180) ------------------------

    async def read_versioned(self, key: str) -> tuple[bytes, str] | None:
        validate_key(key)
        item = await asyncio.to_thread(self._live_item, key)
        value = bytes(item["v"]["B"]) if item is not None else None
        self._audit.read(key, hit=value is not None)
        if value is None:
            return None
        # Hash the live ``v`` (path-independent) rather than trust the
        # stored ``ver``: ``ver`` is the optimisation that makes
        # write/delete_versioned one round trip; ``read_versioned``
        # returns the bytes anyway, so hashing here costs one sha256 and
        # avoids drift if a future code path forgets to refresh ``ver``.
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
        ttl = self._ttl(ttl_seconds)
        from botocore.exceptions import ClientError

        now = {"N": str(time.time())}
        kw: dict[str, Any] = {"TableName": self._table, "Item": self._item(key, value, ttl)}
        if expected_version is None:
            # Create-only: row absent or expired. Mirrors
            # compare_and_set(expected=None) so read/CAS/versioned agree
            # at the expiry boundary (BL-157/BL-177).
            kw["ConditionExpression"] = (
                "attribute_not_exists(pk) OR (attribute_exists(exp) AND exp < :now)"
            )
            kw["ExpressionAttributeValues"] = {":now": now}
        else:
            # Match the server-stored ``ver`` (the content-hash of the
            # live value) AND not expired. Atomic conditional PUT, one
            # round trip. A pre-BL-180 row without ``ver`` cannot be
            # versioned-written until a plain write() rewrites it
            # (documented; see memory/README.md and LIMITATIONS.md L17).
            kw["ConditionExpression"] = "ver = :e AND (attribute_not_exists(exp) OR exp >= :now)"
            kw["ExpressionAttributeValues"] = {
                ":e": {"S": expected_version},
                ":now": now,
            }
        try:
            await asyncio.to_thread(lambda: self._db.put_item(**kw))
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        self._audit.write(key, value_bytes=len(value), ttl_seconds=ttl)
        return self._token(value)

    async def delete_versioned(self, key: str, expected_version: str) -> bool:
        validate_key(key)
        from botocore.exceptions import ClientError

        try:
            await asyncio.to_thread(
                lambda: self._db.delete_item(
                    TableName=self._table,
                    Key={"pk": {"S": self._pk(key)}},
                    # Match ``ver`` and not-expired, mirroring the
                    # compare_and_delete match-branch with the version
                    # attribute instead of byte equality. An expired row
                    # is absent (parity with read()/CAS).
                    ConditionExpression=("ver = :e AND (attribute_not_exists(exp) OR exp >= :now)"),
                    ExpressionAttributeValues={
                        ":e": {"S": expected_version},
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
        total = len(writes_d) + len(deletes_d)
        if total > _TRANSACT_MAX_ITEMS:
            # DynamoDB TransactWriteItems caps at 100 items. Fail fast at
            # the contract boundary rather than mid-call so the caller
            # sees a clear ValueError, not an opaque ClientError.
            raise ValueError(
                f"transaction has {total} operations; DynamoDB TransactWriteItems caps at "
                f"{_TRANSACT_MAX_ITEMS}"
            )
        from botocore.exceptions import ClientError

        items: list[dict[str, Any]] = []
        now_n = {"N": str(time.time())}
        for key, w in writes_d.items():
            ttl = self._ttl(w.ttl_seconds)
            put_kw: dict[str, Any] = {
                "TableName": self._table,
                "Item": self._item(key, w.value, ttl),
            }
            if w.expected_version is None:
                put_kw["ConditionExpression"] = (
                    "attribute_not_exists(pk) OR (attribute_exists(exp) AND exp < :now)"
                )
                put_kw["ExpressionAttributeValues"] = {":now": now_n}
            else:
                put_kw["ConditionExpression"] = (
                    "ver = :e AND (attribute_not_exists(exp) OR exp >= :now)"
                )
                put_kw["ExpressionAttributeValues"] = {
                    ":e": {"S": w.expected_version},
                    ":now": now_n,
                }
            items.append({"Put": put_kw})
        for key, d in deletes_d.items():
            items.append(
                {
                    "Delete": {
                        "TableName": self._table,
                        "Key": {"pk": {"S": self._pk(key)}},
                        "ConditionExpression": (
                            "ver = :e AND (attribute_not_exists(exp) OR exp >= :now)"
                        ),
                        "ExpressionAttributeValues": {
                            ":e": {"S": d.expected_version},
                            ":now": now_n,
                        },
                    }
                }
            )
        try:
            await asyncio.to_thread(lambda: self._db.transact_write_items(TransactItems=items))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
                # A per-item ConditionalCheckFailed cancels the whole
                # transaction (the BL-180 no-op contract). Discriminate
                # by *whitelisting* the infrastructure cancellation codes
                # that must propagate (capacity, throttle, validation,
                # transaction conflict, item-collection-size-limit) and
                # treating everything else (ConditionalCheckFailed, the
                # marker string ``"None"`` for non-failing items in a
                # mixed batch, an actually-null ``Code``, or a missing /
                # absent ``CancellationReasons`` field that some SDK
                # versions omit) as the documented no-op signal. This
                # is a strict narrowing of the prior accept-list check:
                # a real precondition miss is now still a no-op when
                # ``CancellationReasons`` is absent (P1 review fix on
                # PR #50) and is also a no-op when AWS records the
                # non-failing items' ``Code`` as null rather than
                # ``"None"``.
                _INFRA_CODES = {
                    "ItemCollectionSizeLimitExceeded",
                    "TransactionConflict",
                    "ProvisionedThroughputExceeded",
                    "ThrottlingError",
                    "ValidationError",
                }
                reasons = exc.response.get("CancellationReasons", []) or []
                if not any(r.get("Code") in _INFRA_CODES for r in reasons):
                    return None
            raise
        out: dict[str, str] = {}
        for key, w in writes_d.items():
            out[key] = self._token(w.value)
            self._audit.write(
                key,
                value_bytes=len(w.value),
                ttl_seconds=self._ttl(w.ttl_seconds),
            )
        for key in deletes_d:
            self._audit.delete(key, existed=True)
        return out

    def _sweep_sync(self) -> int:
        removed = 0
        start: dict[str, Any] | None = None
        now = time.time()
        while True:
            resp = self._scan_page(start, None)
            for item in resp.get("Items", []):
                exp = item.get("exp", {}).get("N")
                if is_expired(now, float(exp) if exp is not None else None):
                    self._db.delete_item(TableName=self._table, Key={"pk": item["pk"]})
                    removed += 1
            start = resp.get("LastEvaluatedKey")
            if not start:
                break
        return removed

    async def sweep_expired(self) -> int:
        return await asyncio.to_thread(self._sweep_sync)
