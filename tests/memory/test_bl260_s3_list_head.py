"""BL-260 (fifteenth audit): S3 list_keys / scan filter by HEAD, not GET.

``_all_live_keys`` and ``_scan_sync`` called ``_get_live`` (a full
``GetObject``) per listed object just to read the ``expires-at`` metadata
and discard the body. They now use ``_head_live`` (a metadata-only
``HeadObject``), the pattern the sweep paths already use. The regression
guard patches ``_get_live`` to raise: listing must succeed without it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from memory.s3 import S3Store
from memory.types import Namespace

moto = pytest.importorskip("moto")
import boto3  # noqa: E402

_BUCKET = "test-bucket"


@pytest.fixture
def s3_client() -> Iterator[Any]:
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield client


async def test_list_keys_and_scan_do_not_get_object_bodies(s3_client: Any) -> None:
    store = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    await store.write("a", b"AAA")
    await store.write("b", b"BBB")

    def _boom(_key: str) -> bytes | None:
        raise AssertionError("listing must not call _get_live (full-body GET)")

    store._get_live = _boom  # type: ignore[method-assign]

    assert await store.list_keys() == ["a", "b"]
    cursor, page = await store.scan()
    assert page == ["a", "b"]
    assert cursor == ""


async def test_list_keys_excludes_expired_via_head(s3_client: Any) -> None:
    # A live key and an expired key; listing (HEAD-based) excludes the
    # expired one without a body GET.
    store = S3Store(Namespace(name="ns2", workload="w"), _BUCKET, client=s3_client)
    await store.write("live", b"L")
    await store.write("dead", b"D", ttl_seconds=0.01)
    import asyncio

    await asyncio.sleep(0.05)
    assert await store.list_keys() == ["live"]
