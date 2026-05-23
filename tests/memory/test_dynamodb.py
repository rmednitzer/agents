"""Tests for memory.dynamodb.DynamoDBStore (BL-033), via moto."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from harness.sinks import MemorySink
from memory.dynamodb import DynamoDBStore
from memory.store import (
    BatchMemoryStore,
    CASMemoryStore,
    ContentAddressableStore,
    MemoryStore,
    ScannableStore,
    SweepableStore,
    TxnWrite,
    VersionedMemoryStore,
)
from memory.types import Namespace

moto = pytest.importorskip("moto")
import boto3  # noqa: E402

_TABLE = "kv"


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


def _store(client: object, name: str = "ns", **kw: object) -> DynamoDBStore:
    return DynamoDBStore(Namespace(name=name, workload="w"), _TABLE, client=client, **kw)


@pytest.mark.asyncio
async def test_satisfies_protocols(ddb_client: object) -> None:
    s = _store(ddb_client)
    assert isinstance(s, MemoryStore)
    assert isinstance(s, BatchMemoryStore)
    assert isinstance(s, ScannableStore)
    assert isinstance(s, ContentAddressableStore)
    assert isinstance(s, CASMemoryStore)
    assert isinstance(s, SweepableStore)
    assert isinstance(s, VersionedMemoryStore)


@pytest.mark.asyncio
async def test_roundtrip_and_lazy_ttl(ddb_client: object) -> None:
    s = _store(ddb_client, consistent_read=True)
    await s.write("k", b"\x00v\xff")
    assert await s.read("k") == b"\x00v\xff"
    await s.write("t", b"v", ttl_seconds=0.05)
    await asyncio.sleep(0.1)
    assert await s.read("t") is None  # lazily expired (Dynamo TTL lags)
    await s.delete("k")
    assert await s.read("k") is None


@pytest.mark.asyncio
async def test_namespace_isolation(ddb_client: object) -> None:
    a = DynamoDBStore(Namespace(name="a", workload="w"), _TABLE, client=ddb_client)
    b = DynamoDBStore(Namespace(name="b", workload="w"), _TABLE, client=ddb_client)
    await a.write("k", b"a")
    await b.write("k", b"b")
    assert await a.read("k") == b"a"
    assert await b.read("k") == b"b"
    assert await a.list_keys() == ["k"]


@pytest.mark.asyncio
async def test_batch_scan_content(ddb_client: object) -> None:
    s = _store(ddb_client)
    await s.mset({"k1": b"1", "k2": b"2", "k3": b"3"})
    assert await s.mget(["k1", "x", "k3"]) == [b"1", None, b"3"]
    await s.mdelete(["k2"])
    assert sorted(await s.list_keys()) == ["k1", "k3"]
    key = await s.write_content(b"blob")

    seen: list[str] = []
    cursor = ""
    while True:
        cursor, page = await s.scan(cursor=cursor, count=2)
        seen.extend(page)
        if not cursor:
            break
    assert set(seen) == {"k1", "k3", key}


@pytest.mark.asyncio
async def test_conditional_cas(ddb_client: object) -> None:
    s = _store(ddb_client)
    assert await s.compare_and_set("c", None, b"v1") is True
    assert await s.compare_and_set("c", None, b"v2") is False
    assert await s.compare_and_set("c", b"v1", b"v2") is True
    assert await s.read("c") == b"v2"
    assert await s.compare_and_delete("c", b"nope") is False
    assert await s.compare_and_delete("c", b"v2") is True
    assert await s.read("c") is None


@pytest.mark.asyncio
async def test_cas_treats_expired_row_as_absent(ddb_client: object) -> None:
    """Read/CAS parity: an expired-but-present row counts as absent."""
    s = _store(ddb_client)
    await s.write("k", b"old", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    assert await s.read("k") is None
    # CAS-create must succeed against the expired row...
    assert await s.compare_and_set("k", None, b"new") is True
    assert await s.read("k") == b"new"
    # ...and compare-and-delete against an expired row must fail.
    await s.write("e", b"x", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    assert await s.compare_and_delete("e", b"x") is False


@pytest.mark.asyncio
async def test_batch_write_retries_unprocessed_items(ddb_client: object) -> None:
    """mset retries UnprocessedItems instead of silently dropping them."""
    s = _store(ddb_client)
    real = s._db.batch_write_item
    calls = {"n": 0}

    def flaky(**kw: object) -> object:
        calls["n"] += 1
        resp = real(**kw)  # actually process (PutRequest is idempotent)
        if calls["n"] == 1:
            # ...but report one item as throttled so the retry path runs.
            ri = next(iter(kw["RequestItems"].values()))  # type: ignore[union-attr]
            return {"UnprocessedItems": {s._table: ri[:1]}}
        return resp

    s._db.batch_write_item = flaky  # type: ignore[attr-defined]
    await s.mset({"a": b"1", "b": b"2"})
    assert calls["n"] >= 2  # retried
    assert await s.read("a") == b"1"
    assert await s.read("b") == b"2"


@pytest.mark.asyncio
async def test_transact_no_op_on_missing_cancellation_reasons(
    ddb_client: object,
) -> None:
    """Some SDK/service combinations omit ``CancellationReasons`` on a
    cancelled transaction. The whitelist-based discriminator must still
    map "no infrastructure code observed" to the BL-180 no-op contract
    (return None), not raise (P1 review fix on PR #50)."""
    from botocore.exceptions import ClientError

    s = _store(ddb_client, consistent_read=True)
    real = s._db.transact_write_items

    def stripped(**kw: object) -> object:
        try:
            return real(**kw)
        except ClientError as exc:
            # Re-raise with CancellationReasons stripped, simulating the
            # SDK/service combination that omits the field.
            new_response = {k: v for k, v in exc.response.items() if k != "CancellationReasons"}
            raise ClientError(new_response, exc.operation_name) from None

    s._db.transact_write_items = stripped  # type: ignore[attr-defined]
    await s.write("k", b"v")  # row exists
    # Precondition: "must be absent" — fails for the existing row.
    out = await s.transact(writes={"k": TxnWrite(value=b"new")})
    assert out is None
    assert await s.read("k") == b"v"


@pytest.mark.asyncio
async def test_transact_no_op_on_null_code_in_cancellation_reasons(
    ddb_client: object,
) -> None:
    """When one item fails a condition and the others have a null
    ``Code`` (rather than the marker string ``"None"``), the
    discriminator must still treat it as the no-op contract (P1 review
    fix on PR #50)."""
    from botocore.exceptions import ClientError

    s = _store(ddb_client, consistent_read=True)
    real = s._db.transact_write_items

    def null_marker(**kw: object) -> object:
        try:
            return real(**kw)
        except ClientError as exc:
            # Rewrite non-failing items' Code to None (the documented
            # "successful entries can have a null code" case).
            new_response = dict(exc.response)
            reasons = list(new_response.get("CancellationReasons", []))
            new_response["CancellationReasons"] = [
                {**r, "Code": None} if r.get("Code") == "None" else r for r in reasons
            ]
            raise ClientError(new_response, exc.operation_name) from None

    s._db.transact_write_items = null_marker  # type: ignore[attr-defined]
    await s.write("a", b"v-a")  # row exists -> "must be absent" precondition fails
    out = await s.transact(
        writes={
            "a": TxnWrite(value=b"new-a"),  # fails (exists)
            "b": TxnWrite(value=b"new-b"),  # would succeed alone; null Code
        }
    )
    assert out is None
    assert await s.read("a") == b"v-a"
    assert await s.read("b") is None


@pytest.mark.asyncio
async def test_transact_raises_on_infrastructure_cancellation(
    ddb_client: object,
) -> None:
    """An infrastructure cancellation code (e.g. ProvisionedThroughputExceeded)
    must propagate as ``ClientError``, not be silently swallowed as a
    no-op. The whitelist guards against masking a real failure as a
    precondition miss."""
    from botocore.exceptions import ClientError

    s = _store(ddb_client, consistent_read=True)

    def throttling(**kw: object) -> object:
        raise ClientError(
            {
                "Error": {"Code": "TransactionCanceledException", "Message": "x"},
                "CancellationReasons": [
                    {"Code": "ProvisionedThroughputExceeded", "Message": "throttled"}
                ],
            },
            "TransactWriteItems",
        )

    s._db.transact_write_items = throttling  # type: ignore[attr-defined]
    with pytest.raises(ClientError) as ei:
        await s.transact(writes={"k": TxnWrite(value=b"v")})
    assert ei.value.response["Error"]["Code"] == "TransactionCanceledException"


@pytest.mark.asyncio
async def test_write_versioned_against_legacy_row_without_ver_attribute(
    ddb_client: object,
) -> None:
    """A row written before BL-180 has no ``ver`` attribute, so
    write_versioned must refuse it (no silent success) and a plain
    write() must restamp ``ver`` to unblock subsequent versioned writes
    (the documented migration contract; ``LIMITATIONS.md`` L17 +
    ``memory/README.md`` Versioned/transactional scope)."""
    s = _store(ddb_client, consistent_read=True)
    # Simulate a legacy row by writing directly via boto3 without ``ver``.
    ddb_client.put_item(  # type: ignore[attr-defined]
        TableName=_TABLE, Item={"pk": {"S": "ns::legacy"}, "v": {"B": b"old"}}
    )
    # read_versioned still works (hashes the live ``v``).
    rv = await s.read_versioned("legacy")
    assert rv is not None
    legacy_value, legacy_token = rv
    assert legacy_value == b"old"
    # write_versioned with the correct hash fails: ``ver`` is absent so
    # the conditional expression ``ver = :e`` does not match.
    assert await s.write_versioned("legacy", b"new", expected_version=legacy_token) is None
    # A plain write() upgrades the row by stamping ``ver``.
    await s.write("legacy", b"upgraded")
    rv2 = await s.read_versioned("legacy")
    assert rv2 is not None
    upgraded_token = rv2[1]
    # Now versioned writes succeed.
    new_token = await s.write_versioned("legacy", b"final", expected_version=upgraded_token)
    assert new_token is not None
    assert (await s.read("legacy")) == b"final"


@pytest.mark.asyncio
async def test_audit_events(ddb_client: object) -> None:
    base = {
        "workload": "w",
        "contract": "c",
        "contract_version": "1",
        "trace_id": "t",
        "span_id": "s",
    }
    sink = MemorySink()
    s = _store(ddb_client, sink=sink, base_event_fields=base)
    await s.write("k", b"v")
    await s.read("k")
    await s.delete("k")
    assert [e.kind for e in sink.events] == [
        "memory_write",
        "memory_read",
        "memory_delete",
    ]
