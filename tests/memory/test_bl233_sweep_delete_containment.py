"""Thirteenth code audit (ADR 0023): sweep per-item DELETE containment.

BL-233 extends the BL-227 fan-out per-member failure containment class
from ``evict_to_capacity`` to the sibling ``sweep_expired`` path on the
two network adapters that issue a per-item network DELETE inside a
Python loop: ``S3Store._sweep_sync`` (``delete_object``) and
``DynamoDBStore._sweep_sync`` (``delete_item``).

Pre-fix, the per-item DELETE was bare: a single transient backend error
(S3 ``SlowDown`` / throttle, DynamoDB ``ProvisionedThroughputExceeded``,
a network blip) on one expired item propagated out of the loop and
aborted the entire sweep pass, so every later expired item in the same
listing / scan was left un-swept for the cycle and the count of
already-deleted items was discarded (the function raised instead of
returning). This is precisely the question ADR 0020 / 0021 / 0022
deferred from the ``_head_metadata`` (BL-229) scope ("should the parent
sweep be best-effort for transient errors too?").

The fix contains the per-item DELETE (``try/except Exception: continue``)
so the pass completes best-effort, counting only the successful deletes;
the failed item stays alive and the next ``TTLSweeper`` interval retries
it (the BL-199 resilience contract, extended one level down). The
inspection step stays fail-loud (the S3 HEAD via ``_head_metadata``, the
DynamoDB ``Scan``), so an un-inspectable object (real AccessDenied /
NoSuchBucket) still surfaces; only the idempotent DELETE action is
best-effort, exactly as ``evict_to_capacity`` already is. ``BaseException``
(KeyboardInterrupt / SystemExit / asyncio.CancelledError) still
propagates per the BL-165 / BL-223 invariant.

The other in-tree adapters' sweeps are bulk (no per-item network loop):
InMemoryStore (dict ops), SQLiteStore (one SQL DELETE), Redis (``zrem`` /
pipeline). The finding is the two adapters with the per-item-loop shape,
the same reason BL-227 was S3-specific.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

moto = pytest.importorskip("moto")
import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

from memory.dynamodb import DynamoDBStore  # noqa: E402
from memory.s3 import S3Store  # noqa: E402
from memory.types import Namespace  # noqa: E402

_BUCKET = "test-bucket"
_TABLE = "kv"


def _client_error(code: str, op: str) -> ClientError:
    """A transient-shaped boto3 ClientError (throttle / blip)."""
    return ClientError({"Error": {"Code": code, "Message": "transient"}}, op)


# ---- S3 fixtures and flaky clients ----------------------------------------


@pytest.fixture
def s3_client() -> Iterator[object]:
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield client


class _S3FlakyDelete:
    """Forwards to a real S3 client but raises a transient ``ClientError``
    on the DELETE of one specific full S3 key."""

    def __init__(self, real: object, fail_on_key: str) -> None:
        self._real = real
        self._fail_on_key = fail_on_key
        self.exceptions = real.exceptions  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def delete_object(self, *, Bucket: str, Key: str) -> object:
        if Key == self._fail_on_key:
            raise _client_error("SlowDown", "DeleteObject")
        return self._real.delete_object(Bucket=Bucket, Key=Key)  # type: ignore[attr-defined]


class _S3AlwaysFailDelete:
    def __init__(self, real: object) -> None:
        self._real = real
        self.exceptions = real.exceptions  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def delete_object(self, **kw: object) -> object:
        raise _client_error("SlowDown", "DeleteObject")


class _S3ExitOnDelete:
    def __init__(self, real: object) -> None:
        self._real = real
        self.exceptions = real.exceptions  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def delete_object(self, **kw: object) -> object:
        raise SystemExit("terminal")


def _exists(client: object, key: str) -> bool:
    try:
        client.head_object(Bucket=_BUCKET, Key=key)  # type: ignore[attr-defined]
        return True
    except ClientError as exc:
        # Return False only for a genuine not-found; re-raise any other
        # backend error (wrong bucket, AccessDenied, outage) so a real
        # failure fails the test loudly instead of masquerading as
        # "object absent" and letting `assert not _exists(...)` pass for
        # the wrong reason. Mirrors the production fail-loud-on-inspection
        # stance this suite exercises (`S3Store._head_metadata` /
        # `_get_live`: only `404` / `NoSuchKey` is absent, the rest
        # propagate).
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in ("NoSuchKey", "404", "NotFound"):
            return False
        raise


# ---- S3: BL-233 sweep containment ------------------------------------------


@pytest.mark.asyncio
async def test_s3_sweep_contains_per_object_delete_failure(s3_client: object) -> None:
    # Four expired objects; the DELETE of k1 fails transiently. Pre-fix,
    # the sweep aborted on k1 and k2 / k3 (which sort after it) were left
    # un-swept. The fix skips k1 and sweeps the rest, returning the count
    # of actual successes without raising.
    setup = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    for i in range(4):
        await setup.write(f"k{i}", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)

    flaky = _S3FlakyDelete(s3_client, fail_on_key="ns/k1")
    s = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=flaky)
    swept = await s.sweep_expired()  # must not raise

    assert swept == 3  # k0, k2, k3 swept; k1's delete failed
    assert _exists(s3_client, "ns/k1")  # the failed object stays alive
    for gone in ("ns/k0", "ns/k2", "ns/k3"):
        assert not _exists(s3_client, gone)


@pytest.mark.asyncio
async def test_s3_sweep_all_deletes_failing_returns_zero_no_raise(s3_client: object) -> None:
    setup = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    for i in range(3):
        await setup.write(f"k{i}", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)

    s = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=_S3AlwaysFailDelete(s3_client))
    swept = await s.sweep_expired()  # must not raise

    assert swept == 0
    for i in range(3):
        assert _exists(s3_client, f"ns/k{i}")  # all stay alive, retried next cycle


@pytest.mark.asyncio
async def test_s3_sweep_happy_path_unchanged(s3_client: object) -> None:
    # The per-item try/except must not regress the no-error path.
    s = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    for i in range(3):
        await s.write(f"k{i}", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    swept = await s.sweep_expired()
    assert swept == 3
    assert await s.list_keys() == []


@pytest.mark.asyncio
async def test_s3_sweep_base_exception_propagates(s3_client: object) -> None:
    # BL-165 / BL-223: containment catches Exception, not BaseException.
    setup = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    await setup.write("k0", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    s = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=_S3ExitOnDelete(s3_client))
    with pytest.raises(SystemExit):
        await s.sweep_expired()


# ---- DynamoDB fixtures and flaky clients -----------------------------------


@pytest.fixture
def ddb_client() -> Iterator[object]:
    with moto.mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=_TABLE,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


class _DdbFlakyDelete:
    """Forwards to a real DynamoDB client but raises a transient
    ``ClientError`` on the DELETE of one specific pk."""

    def __init__(self, real: object, fail_on_pk: str) -> None:
        self._real = real
        self._fail_on_pk = fail_on_pk
        self.exceptions = real.exceptions  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def delete_item(self, *, TableName: str, Key: dict[str, Any]) -> object:
        if Key["pk"]["S"] == self._fail_on_pk:
            raise _client_error("ProvisionedThroughputExceededException", "DeleteItem")
        return self._real.delete_item(TableName=TableName, Key=Key)  # type: ignore[attr-defined]


class _DdbAlwaysFailDelete:
    def __init__(self, real: object) -> None:
        self._real = real
        self.exceptions = real.exceptions  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def delete_item(self, **kw: object) -> object:
        raise _client_error("ProvisionedThroughputExceededException", "DeleteItem")


class _DdbExitOnDelete:
    def __init__(self, real: object) -> None:
        self._real = real
        self.exceptions = real.exceptions  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def delete_item(self, **kw: object) -> object:
        raise SystemExit("terminal")


def _item_exists(client: object, pk: str) -> bool:
    resp = client.get_item(TableName=_TABLE, Key={"pk": {"S": pk}})  # type: ignore[attr-defined]
    return "Item" in resp


# ---- DynamoDB: BL-233 sweep containment ------------------------------------


@pytest.mark.asyncio
async def test_ddb_sweep_contains_per_item_delete_failure(ddb_client: object) -> None:
    setup = DynamoDBStore(Namespace(name="cap", workload="w"), _TABLE, client=ddb_client)
    for i in range(4):
        await setup.write(f"k{i}", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)

    flaky = _DdbFlakyDelete(ddb_client, fail_on_pk="cap::k1")
    s = DynamoDBStore(Namespace(name="cap", workload="w"), _TABLE, client=flaky)
    swept = await s.sweep_expired()  # must not raise

    assert swept == 3  # k1's delete failed; the other three swept
    assert _item_exists(ddb_client, "cap::k1")  # the failed item stays alive
    for gone in ("cap::k0", "cap::k2", "cap::k3"):
        assert not _item_exists(ddb_client, gone)


@pytest.mark.asyncio
async def test_ddb_sweep_all_deletes_failing_returns_zero_no_raise(ddb_client: object) -> None:
    setup = DynamoDBStore(Namespace(name="cap", workload="w"), _TABLE, client=ddb_client)
    for i in range(3):
        await setup.write(f"k{i}", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)

    s = DynamoDBStore(
        Namespace(name="cap", workload="w"), _TABLE, client=_DdbAlwaysFailDelete(ddb_client)
    )
    swept = await s.sweep_expired()  # must not raise

    assert swept == 0
    for i in range(3):
        assert _item_exists(ddb_client, f"cap::k{i}")


@pytest.mark.asyncio
async def test_ddb_sweep_happy_path_unchanged(ddb_client: object) -> None:
    s = DynamoDBStore(Namespace(name="cap", workload="w"), _TABLE, client=ddb_client)
    for i in range(3):
        await s.write(f"k{i}", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    swept = await s.sweep_expired()
    assert swept == 3
    assert await s.list_keys() == []


@pytest.mark.asyncio
async def test_ddb_sweep_base_exception_propagates(ddb_client: object) -> None:
    setup = DynamoDBStore(Namespace(name="cap", workload="w"), _TABLE, client=ddb_client)
    await setup.write("k0", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    s = DynamoDBStore(
        Namespace(name="cap", workload="w"), _TABLE, client=_DdbExitOnDelete(ddb_client)
    )
    with pytest.raises(SystemExit):
        await s.sweep_expired()
