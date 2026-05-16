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
