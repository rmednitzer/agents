"""Regression tests for the ADR 0011 audit follow-ups (memory).

- BL-177: DynamoDB ``compare_and_set`` (match branch) and
  ``compare_and_delete`` use ``exp >= :now`` so the CAS live boundary
  matches ``_live_item`` (expired only when ``now > exp``), the
  read-vs-CAS boundary class BL-157/BL-168 fixed elsewhere.
- BL-178: SQLite ``mset``/``mdelete`` of an empty batch is a no-op and
  does not take the database write lock.
- BL-188: ``InMemoryStore`` / ``SQLiteStore`` ``list_keys`` and
  ``scan`` use the same ``now <= exp`` live boundary as ``read`` /
  ``sweep_expired``; an entry at the exact expiry instant that ``read``
  still returns must not be missing from a listing (the read-vs-listing
  twin of the BL-157/168/177 read-vs-CAS boundary class).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest

from harness.sinks import MemorySink
from memory.sqlite import SQLiteStore
from memory.types import Namespace


def _ns(name: str = "ns") -> Namespace:
    return Namespace(name=name, workload="w")


# --- BL-178: SQLite empty-batch no-op --------------------------------


@pytest.mark.asyncio
async def test_sqlite_empty_mset_mdelete_are_noops() -> None:
    sink = MemorySink()
    store = SQLiteStore(
        _ns(),
        base_event_fields={
            "workload": "w",
            "contract": "c",
            "contract_version": "1",
            "trace_id": "t",
            "span_id": "s",
        },
        sink=sink,
    )
    await store.mset({})
    await store.mdelete([])
    # No rows touched, no audit events emitted, store still usable.
    assert sink.events == []
    await store.write("k", b"v")
    assert await store.read("k") == b"v"
    store.close()


# --- BL-177: DynamoDB CAS TTL boundary parity ------------------------

moto = pytest.importorskip("moto")
import boto3  # noqa: E402

from memory.dynamodb import DynamoDBStore  # noqa: E402

_TABLE = "kv"


class _RecordingClient:
    """Delegates to the moto client, capturing condition expressions."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.conditions: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def put_item(self, **kw: Any) -> Any:
        if "ConditionExpression" in kw:
            self.conditions.append(kw["ConditionExpression"])
        return self._inner.put_item(**kw)

    def delete_item(self, **kw: Any) -> Any:
        if "ConditionExpression" in kw:
            self.conditions.append(kw["ConditionExpression"])
        return self._inner.delete_item(**kw)


@pytest.fixture
def rec_client() -> Iterator[_RecordingClient]:
    with moto.mock_aws():
        inner = boto3.client("dynamodb", region_name="us-east-1")
        inner.create_table(
            TableName=_TABLE,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield _RecordingClient(inner)


@pytest.mark.asyncio
async def test_dynamodb_cas_uses_inclusive_expiry_boundary(
    rec_client: _RecordingClient,
) -> None:
    store = DynamoDBStore(_ns(), _TABLE, client=rec_client, consistent_read=True)
    await store.write("k", b"v", ttl_seconds=3600)
    assert await store.compare_and_set("k", b"v", b"v2") is True
    assert await store.read("k") == b"v2"
    assert await store.compare_and_delete("k", b"v2") is True
    # The match-branch CAS and the compare-and-delete both gate on
    # ``exp >= :now`` (inclusive), matching _live_item's live boundary.
    gated = [c for c in rec_client.conditions if ":now" in c]
    assert gated
    assert all(">= :now" in c for c in gated)
    assert not any("> :now" in c.replace(">= :now", "") for c in gated)


@pytest.mark.asyncio
async def test_dynamodb_cas_expired_row_is_absent(rec_client: _RecordingClient) -> None:
    """The boundary change does not regress the expired-is-absent rule."""
    store = DynamoDBStore(_ns(), _TABLE, client=rec_client, consistent_read=True)
    await store.write("k", b"v", ttl_seconds=0.05)
    time.sleep(0.1)
    assert await store.read("k") is None
    # An expired row is absent: a value-match CAS must fail.
    assert await store.compare_and_set("k", b"v", b"v2") is False


# --- BL-188: read-vs-listing expiry boundary parity ------------------

import memory.inmemory as _inmem_mod  # noqa: E402
import memory.sqlite as _sqlite_mod  # noqa: E402
from memory.inmemory import InMemoryStore  # noqa: E402


@pytest.mark.asyncio
async def test_inmemory_listing_agrees_with_read_at_expiry_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(_inmem_mod.time, "time", lambda: clock["t"])
    store = InMemoryStore(_ns())
    await store.write("k", b"v", ttl_seconds=10)  # expires_at == 1010

    clock["t"] = 1010.0  # the exact expiry instant: read treats it live
    assert await store.read("k") == b"v"
    assert "k" in await store.list_keys()
    _, page = await store.scan()
    assert "k" in page

    clock["t"] = 1010.001  # just past expiry: gone everywhere
    assert await store.read("k") is None
    assert await store.list_keys() == []
    assert (await store.scan())[1] == []


@pytest.mark.asyncio
async def test_sqlite_listing_agrees_with_read_at_expiry_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"t": 2000.0}
    monkeypatch.setattr(_sqlite_mod.time, "time", lambda: clock["t"])
    store = SQLiteStore(_ns())
    try:
        await store.write("k", b"v", ttl_seconds=10)  # expires_at == 2010

        clock["t"] = 2010.0  # exact expiry instant: read treats it live
        assert await store.read("k") == b"v"
        assert "k" in await store.list_keys()
        _, page = await store.scan()
        assert "k" in page

        clock["t"] = 2010.001  # just past expiry: gone everywhere
        assert await store.read("k") is None
        assert await store.list_keys() == []
        assert (await store.scan())[1] == []
    finally:
        store.close()
