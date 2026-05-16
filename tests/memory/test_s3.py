"""Tests for memory.s3.S3Store (BL-032), via moto."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from harness.sinks import MemorySink
from memory.s3 import S3Store
from memory.store import (
    BatchMemoryStore,
    ContentAddressableStore,
    MemoryStore,
    ScannableStore,
    SweepableStore,
)
from memory.types import Namespace

moto = pytest.importorskip("moto")
import boto3  # noqa: E402

_BUCKET = "test-bucket"


@pytest.fixture
def s3_client() -> Iterator[object]:
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield client


def _store(client: object, name: str = "ns") -> S3Store:
    return S3Store(Namespace(name=name, workload="w"), _BUCKET, client=client)


@pytest.mark.asyncio
async def test_satisfies_protocols(s3_client: object) -> None:
    s = _store(s3_client)
    assert isinstance(s, MemoryStore)
    assert isinstance(s, BatchMemoryStore)
    assert isinstance(s, ScannableStore)
    assert isinstance(s, ContentAddressableStore)
    assert isinstance(s, SweepableStore)


@pytest.mark.asyncio
async def test_roundtrip_and_lazy_ttl(s3_client: object) -> None:
    s = _store(s3_client)
    await s.write("k", b"\x00bin\xff")
    assert await s.read("k") == b"\x00bin\xff"
    await s.write("t", b"v", ttl_seconds=0.05)
    await asyncio.sleep(0.1)
    assert await s.read("t") is None  # lazily expired on access
    await s.delete("k")
    assert await s.read("k") is None


@pytest.mark.asyncio
async def test_prefix_isolation_and_list(s3_client: object) -> None:
    a = S3Store(Namespace(name="a", workload="w"), _BUCKET, client=s3_client)
    b = S3Store(Namespace(name="b", workload="w"), _BUCKET, client=s3_client)
    await a.write("doc-1", b"x")
    await a.write("doc-2", b"x")
    await b.write("doc-1", b"y")
    assert await a.list_keys("doc-") == ["doc-1", "doc-2"]
    assert await b.read("doc-1") == b"y"


@pytest.mark.asyncio
async def test_batch_content_scan_sweep(s3_client: object) -> None:
    s = _store(s3_client)
    await s.mset({"k1": b"1", "k2": b"2"})
    assert await s.mget(["k1", "x", "k2"]) == [b"1", None, b"2"]
    key = await s.write_content(b"blob")
    assert await s.read(key) == b"blob"
    cursor, page = await s.scan(count=100)
    assert "k1" in page
    assert "k2" in page
    assert cursor == ""

    await s.write("temp", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    assert await s.sweep_expired() >= 1


@pytest.mark.asyncio
async def test_scan_excludes_expired_keys(s3_client: object) -> None:
    """scan() must agree with read()/list_keys() on expiry."""
    s = _store(s3_client)
    await s.write("live", b"v")
    await s.write("dead", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    _, page = await s.scan(count=100)
    assert page == ["live"]


@pytest.mark.asyncio
async def test_non_notfound_client_error_propagates() -> None:
    """Regression: AccessDenied/throttling must not be masked as a miss."""
    from botocore.exceptions import ClientError as _CE

    class _NoSuchKey(_CE):
        pass

    class _Exc:
        ClientError = _CE
        NoSuchKey = _NoSuchKey

    class _FailingClient:
        exceptions = _Exc()

        def get_object(self, **kw: object) -> object:
            raise _CE(
                {"Error": {"Code": "AccessDenied", "Message": "denied"}},
                "GetObject",
            )

    s = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=_FailingClient())
    with pytest.raises(_CE):
        await s.read("k")


@pytest.mark.asyncio
async def test_audit_events(s3_client: object) -> None:
    base = {
        "workload": "w",
        "contract": "c",
        "contract_version": "1",
        "trace_id": "t",
        "span_id": "s",
    }
    sink = MemorySink()
    s = S3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=s3_client,
        sink=sink,
        base_event_fields=base,
    )
    await s.write("k", b"v")
    await s.read("k")
    await s.delete("k")
    assert [e.kind for e in sink.events] == [
        "memory_write",
        "memory_read",
        "memory_delete",
    ]
