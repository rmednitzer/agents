"""BL-229 (eleventh audit, ADR 0021): S3 metadata-scan HEAD not-found
containment (LIST -> HEAD TOCTOU).

`S3Store._sweep_sync` and `BoundedS3Store._collect_live_sync` LIST the
namespace prefix and then `head_object(...)` each listed key directly.
If an object is deleted between the LIST and the HEAD (S3's documented
concurrent-access / eventual-consistency window: another writer, a
concurrent `read` lazy-expiry delete, or a concurrent `sweep_expired`),
HeadObject returns HTTP 404 (`NoSuchKey`) and the raw call crashed the
whole sweep / eviction scan. `_get_live` (the read path) already treats
this not-found case as "absent", but the two metadata-scan loops
bypassed it.

The fix is `S3Store._head_metadata`, which returns the object's user
metadata or `None` when not-found (NoSuchKey / 404 / NotFound),
mirroring `_get_live`'s idiom. Both scan loops skip a `None`. Any other
ClientError (throttle, AccessDenied, outage) still propagates; the fix
is the narrow not-found-consistency resolution, NOT a blanket
best-effort sweep (it deliberately leaves the ADR 0020 "should the
parent sweep be best-effort for transient errors too?" question open).
DeleteObject is S3-idempotent, so only the HEAD needs the guard.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from memory.s3 import BoundedS3Store, S3Store
from memory.types import Namespace

moto = pytest.importorskip("moto")
import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

_BUCKET = "test-bucket"


@pytest.fixture
def s3_client() -> Iterator[object]:
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield client


class _NotFoundHeadClient:
    """Wraps a moto client; raises a 404 ClientError on `head_object`
    for one target key, simulating an object deleted between the LIST
    that produced it and this HEAD. All other calls forward unchanged,
    so the key is still returned by `list_objects_v2`.
    """

    def __init__(self, real: object, fail_on_key: str) -> None:
        self._real = real
        self._fail_on_key = fail_on_key
        self.head_call_count = 0
        self.exceptions = real.exceptions  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def head_object(self, *, Bucket: str, Key: str) -> object:
        self.head_call_count += 1
        if Key == self._fail_on_key:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}},
                "HeadObject",
            )
        return self._real.head_object(Bucket=Bucket, Key=Key)  # type: ignore[attr-defined]


class _TypedNoSuchKeyHeadClient:
    """Wraps a moto client; raises the *typed* `exceptions.NoSuchKey` on
    every `head_object`, exercising the typed-catch branch of
    `_head_metadata` (parity with `_get_live`).
    """

    def __init__(self, real: object) -> None:
        self._real = real
        self.exceptions = real.exceptions  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def head_object(self, **kw: object) -> object:
        raise self._real.exceptions.NoSuchKey(  # type: ignore[attr-defined]
            {"Error": {"Code": "NoSuchKey", "Message": "gone"}},
            "HeadObject",
        )


class _ErrorHeadClient:
    """Wraps a moto client; raises a non-404 ClientError (a transient
    throttle) on every `head_object`. Used to assert a real backend
    error still propagates rather than being swallowed.
    """

    def __init__(self, real: object, code: str = "SlowDown") -> None:
        self._real = real
        self._code = code
        self.exceptions = real.exceptions  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def head_object(self, **kw: object) -> object:
        raise ClientError(
            {"Error": {"Code": self._code, "Message": "throttled"}},
            "HeadObject",
        )


# ---- _head_metadata helper -------------------------------------------------


def test_head_metadata_returns_metadata_for_present_object(s3_client: object) -> None:
    s = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    s3_client.put_object(  # type: ignore[attr-defined]
        Bucket=_BUCKET, Key="ns/k", Body=b"v", Metadata={"expires-at": "123.0"}
    )
    assert s._head_metadata("ns/k") == {"expires-at": "123.0"}


def test_head_metadata_none_on_404(s3_client: object) -> None:
    s = S3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=_NotFoundHeadClient(s3_client, "ns/gone"),
    )
    assert s._head_metadata("ns/gone") is None


def test_head_metadata_none_on_typed_nosuchkey(s3_client: object) -> None:
    s = S3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=_TypedNoSuchKeyHeadClient(s3_client),
    )
    assert s._head_metadata("ns/whatever") is None


def test_head_metadata_propagates_non_404(s3_client: object) -> None:
    s = S3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=_ErrorHeadClient(s3_client),
    )
    with pytest.raises(ClientError):
        s._head_metadata("ns/whatever")


# ---- sweep_expired: TOCTOU skip + real-error propagation -------------------


@pytest.mark.asyncio
async def test_sweep_skips_concurrently_deleted_object(s3_client: object) -> None:
    # "gone" 404s on HEAD (deleted in the LIST->HEAD window); "dies" is
    # expired; "lives" is not. Pre-fix, the 404 on "gone" crashed the
    # whole sweep. The fix skips "gone" and sweeps "dies" only.
    setup = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    await setup.write("gone", b"v")
    await setup.write("dies", b"v", ttl_seconds=0.02)
    await setup.write("lives", b"v", ttl_seconds=100)
    await asyncio.sleep(0.05)

    flaky = _NotFoundHeadClient(s3_client, fail_on_key="ns/gone")
    s = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=flaky)
    swept = await s.sweep_expired()

    assert swept == 1  # only "dies"; "gone" skipped (404), "lives" live
    assert await setup.read("dies") is None
    assert await setup.read("lives") == b"v"
    # "gone" was only 404'd on HEAD, never actually deleted by the sweep.
    assert await setup.read("gone") == b"v"


@pytest.mark.asyncio
async def test_sweep_propagates_non_404_head_error(s3_client: object) -> None:
    # A transient throttle on HEAD is a real backend error and must
    # propagate (the fix is not a blanket best-effort sweep).
    setup = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    await setup.write("k", b"v", ttl_seconds=100)
    s = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=_ErrorHeadClient(s3_client))
    with pytest.raises(ClientError):
        await s.sweep_expired()


@pytest.mark.asyncio
async def test_sweep_happy_path_unchanged(s3_client: object) -> None:
    # The _head_metadata indirection must not regress a normal sweep.
    s = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    await s.write("dies", b"v", ttl_seconds=0.02)
    await s.write("lives", b"v", ttl_seconds=100)
    await asyncio.sleep(0.05)
    assert await s.sweep_expired() == 1
    assert await s.read("dies") is None
    assert await s.read("lives") == b"v"


# ---- evict_to_capacity: collect-phase HEAD TOCTOU (new in BL-229) ----------


@pytest.mark.asyncio
async def test_evict_skips_concurrently_deleted_object(s3_client: object) -> None:
    # BL-227 contained the per-key DELETE loop but not the collect-phase
    # HEAD; a concurrently-deleted object still crashed the eviction
    # scan. k1 404s on HEAD (deleted in the LIST->HEAD window) and is
    # excluded from the live set; eviction proceeds over {k0, k2}.
    setup = BoundedS3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    await setup.write("k0", b"v")
    await setup.write("k1", b"v")
    await setup.write("k2", b"v")

    flaky = _NotFoundHeadClient(s3_client, fail_on_key="ns/k1")
    s = BoundedS3Store(Namespace(name="ns", workload="w"), _BUCKET, client=flaky)
    evicted = await s.evict_to_capacity(1)

    # Live set after skipping k1 is {k0, k2}; cap 1 evicts the oldest, k0.
    assert evicted == 1
    # k0 deleted; k1 only HEAD-404'd (never deleted); k2 remains.
    assert sorted(await setup.list_keys()) == ["k1", "k2"]


@pytest.mark.asyncio
async def test_evict_propagates_non_404_head_error(s3_client: object) -> None:
    setup = BoundedS3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    await setup.write("k0", b"v")
    await setup.write("k1", b"v")
    s = BoundedS3Store(
        Namespace(name="ns", workload="w"), _BUCKET, client=_ErrorHeadClient(s3_client)
    )
    with pytest.raises(ClientError):
        await s.evict_to_capacity(1)
