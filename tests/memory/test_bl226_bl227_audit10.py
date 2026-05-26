"""Tenth code audit (ADR 0020): S3 metadata robustness and audit-vs-raise.

Two findings against the just-merged BL-225 BoundedS3Store wave:

- BL-226: `int(seq_raw)` / `float(exp)` on untrusted S3 user metadata
  was raw `int()` / `float()` and silently bypassed the BL-159 /
  BL-205 / BL-221 non-finite guard. A corrupted or hand-written
  metadata value crashed the read / sweep / eviction scan past the
  documented exception contract (ValueError leaked), and NaN /
  +inf / -inf in `expires-at` permanently masked an object from
  every expiry path because `now > NaN` is always `False`. The
  S3-side helpers `_safe_float` (None on missing / unparseable /
  non-finite) and `_safe_int` (0 on missing / unparseable;
  matches the BL-225 legacy-migration default) now apply at every
  metadata-read boundary in `S3Store._get_live`, `S3Store._sweep_sync`,
  and `BoundedS3Store._collect_live_sync`. Class extension of
  BL-159 / BL-201 / BL-205 / BL-215 / BL-217 / BL-221 to the
  metadata-read trust boundary.

- BL-227: `BoundedS3Store.evict_to_capacity` ran the per-key
  `delete_object` loop with no exception containment, so a
  transient S3 error mid-loop (throttle, access-drift, network
  blip) propagated out of `asyncio.to_thread` and the audit-emit
  loop below was never reached -- partial state mutation with no
  audit, the exact BL-202 / BL-167 audit-vs-raise parity
  violation. The loop now contains per-key failures (parallel to
  BL-222 / BL-223 fan-out containment), collects the actually-
  deleted keys, and emits audit only for those; the function
  returns the count of actual successes. `BaseException`
  (KeyboardInterrupt / SystemExit / asyncio.CancelledError) still
  propagates per the BL-165 / BL-223 invariant. A failed delete
  leaves its key alive; the next TTLSweeper cycle retries it,
  which is the existing TTLSweeper resilience contract from
  BL-199.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from harness.events import MemoryDelete
from harness.sinks import MemorySink
from memory.s3 import BoundedS3Store, S3Store, _safe_float, _safe_int
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


# ---- _safe_float helper ----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("not-a-number", None),
        ("nan", None),
        ("NaN", None),
        ("inf", None),
        ("-inf", None),
        ("Infinity", None),
        ("1.5", 1.5),
        ("0", 0.0),
        ("-1.0", -1.0),
        ("1e10", 1e10),
    ],
)
def test_safe_float_rejects_non_finite_and_unparseable(
    value: str | None, expected: float | None
) -> None:
    result = _safe_float(value)
    assert result == expected, f"_safe_float({value!r}) = {result!r}"


def test_safe_float_explicitly_rejects_nan() -> None:
    # math.isnan(NaN) is True; the helper must reject it.
    assert _safe_float("nan") is None
    # And a Python-emitted NaN string format.
    assert _safe_float(str(float("nan"))) is None


# ---- _safe_int helper ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 0),
        ("", 0),
        ("not-a-number", 0),
        ("1.5", 0),  # int() rejects floats, returns the default
        ("nan", 0),
        ("inf", 0),
        ("0", 0),
        ("1", 1),
        ("-1", -1),
        ("1000000000000000000", 1_000_000_000_000_000_000),
    ],
)
def test_safe_int_rejects_unparseable(value: str | None, expected: int) -> None:
    result = _safe_int(value)
    assert result == expected, f"_safe_int({value!r}) = {result!r}"


# ---- BL-226: parent S3Store metadata robustness ----------------------------


def _write_with_metadata(client: object, key: str, value: bytes, metadata: dict[str, str]) -> None:
    """Helper: write directly via boto3 (bypassing S3Store) to inject a
    metadata value that the adapter would never produce on its own.
    """
    client.put_object(  # type: ignore[attr-defined]
        Bucket=_BUCKET, Key=f"ns/{key}", Body=value, Metadata=metadata
    )


@pytest.mark.asyncio
async def test_corrupted_expires_at_metadata_does_not_crash_read(s3_client: object) -> None:
    # Hand-written non-numeric ``expires-at`` would, under the
    # pre-fix code, raise ValueError out of ``float(exp)`` and leak
    # past the documented exception contract of ``S3Store.read``.
    # The fix treats unparseable metadata as None (no TTL recorded),
    # so the object is still readable.
    s = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    _write_with_metadata(s3_client, "k", b"v", {"expires-at": "not-a-number"})
    assert await s.read("k") == b"v"


@pytest.mark.asyncio
async def test_nan_expires_at_metadata_does_not_mask_from_expiry(s3_client: object) -> None:
    # A hand-written ``expires-at = "nan"`` would, under the pre-fix
    # code, sail through ``float()`` (returns NaN) and then through
    # ``is_expired(now, NaN)`` which evaluates ``now > NaN`` to
    # ``False``: the object is never expired by any code path. The
    # fix treats NaN as None (no TTL recorded), so the object is
    # still live AND remains so until explicitly deleted; the
    # *important* property tested here is that NaN does not act as
    # a "live forever" hack -- if an operator later overwrites with
    # a real TTL, expiry resumes.
    s = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    _write_with_metadata(s3_client, "k", b"v", {"expires-at": "nan"})
    # Read works (no crash, no NaN-evaluated expiry).
    assert await s.read("k") == b"v"


@pytest.mark.asyncio
async def test_corrupted_expires_at_does_not_crash_sweep(s3_client: object) -> None:
    # Parent S3Store sweep path: a single object with corrupt
    # metadata would crash the whole sweep loop. The fix treats
    # the corrupted entry as "no TTL", so it is not swept, and the
    # remaining (validly TTL'd) entries are swept normally.
    import asyncio

    s = S3Store(Namespace(name="ns", workload="w"), _BUCKET, client=s3_client)
    _write_with_metadata(s3_client, "bad", b"v", {"expires-at": "not-a-number"})
    await s.write("dies", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    swept = await s.sweep_expired()
    assert swept == 1  # only the validly-TTL'd one
    assert await s.read("bad") == b"v"  # the corrupt one is still live
    assert await s.read("dies") is None


# ---- BL-226: BoundedS3Store metadata robustness ----------------------------


@pytest.mark.asyncio
async def test_corrupted_insertion_order_metadata_does_not_crash_evict(
    s3_client: object,
) -> None:
    # A corrupted or hand-written ``insertion-order = "abc"`` would,
    # under the pre-fix code, raise ValueError out of ``int(seq_raw)``
    # and crash the eviction scan. The fix treats unparseable
    # ``insertion-order`` as 0 (the BL-225 legacy-migration default),
    # so the corrupted entry simply evicts as the oldest.
    s = BoundedS3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=s3_client,  # type: ignore[arg-type]
    )
    _write_with_metadata(s3_client, "corrupt", b"v", {"insertion-order": "abc"})
    await s.write("fresh1", b"v")
    await s.write("fresh2", b"v")
    # Cap=2: the corrupted entry has insertion-order=0 (oldest),
    # evicts first.
    evicted = await s.evict_to_capacity(2)
    assert evicted == 1
    keys = sorted(await s.list_keys())
    assert "corrupt" not in keys
    assert keys == ["fresh1", "fresh2"]


@pytest.mark.asyncio
async def test_corrupted_expires_at_metadata_does_not_crash_evict(s3_client: object) -> None:
    # In the evict path, a corrupted ``expires-at`` would have
    # crashed ``_collect_live_sync``. The fix treats it as no TTL
    # (object stays in the live count). Cap = 1 evicts the oldest
    # of the two live entries.
    s = BoundedS3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=s3_client,  # type: ignore[arg-type]
    )
    _write_with_metadata(s3_client, "bad_ttl", b"v", {"expires-at": "not-a-number"})
    await s.write("fresh", b"v")
    # Both are live; cap = 1 evicts the older (the corrupted one,
    # because its ``insertion-order`` is absent -> seq = 0).
    evicted = await s.evict_to_capacity(1)
    assert evicted == 1
    keys = sorted(await s.list_keys())
    assert keys == ["fresh"]


@pytest.mark.asyncio
async def test_nan_insertion_order_treated_as_legacy(s3_client: object) -> None:
    # ``insertion-order = "nan"`` is not a valid int, so _safe_int
    # returns 0 (the legacy-migration default). Verifies the
    # int-not-float parsing path: ``int("nan")`` raises ValueError
    # which the helper swallows.
    s = BoundedS3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=s3_client,  # type: ignore[arg-type]
    )
    _write_with_metadata(s3_client, "nan_seq", b"v", {"insertion-order": "nan"})
    await s.write("fresh", b"v")
    evicted = await s.evict_to_capacity(1)
    assert evicted == 1
    # nan_seq evicted first (treated as oldest, seq=0).
    assert sorted(await s.list_keys()) == ["fresh"]


# ---- BL-227: per-item failure containment in evict_to_capacity --------------


class _FlakyClient:
    """Wraps a moto-backed S3 client and injects a ClientError on the
    DELETE of a specified key. Used to exercise the per-item
    failure-containment path of ``BoundedS3Store.evict_to_capacity``
    without mocking the entire boto3 surface.
    """

    def __init__(self, real_client: object, fail_on_key: str) -> None:
        self._real = real_client
        self._fail_on_key = fail_on_key
        self.delete_call_count = 0
        self.exceptions = real_client.exceptions  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        # Default: forward to the real client.
        return getattr(self._real, name)

    def delete_object(self, *, Bucket: str, Key: str) -> object:
        self.delete_call_count += 1
        if Key == self._fail_on_key:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "SlowDown", "Message": "throttled"}},
                "DeleteObject",
            )
        return self._real.delete_object(Bucket=Bucket, Key=Key)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_partial_delete_failure_audits_successes_only(s3_client: object) -> None:
    # The BL-227 audit-vs-raise parity invariant: if some DELETEs
    # in the eviction batch succeed and one fails, audit MUST emit
    # for the successful ones (and not for the failed). Pre-fix,
    # the loop would crash on the first failure, leaving partial
    # state mutation with no audit at all -- the BL-202 / BL-167
    # invariant violation.
    sink = MemorySink()
    base = {
        "workload": "w",
        "contract": "c",
        "contract_version": "1",
        "trace_id": "t",
        "span_id": "s",
    }
    # Write 4 keys via the real client first, so the data exists.
    s_setup = BoundedS3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=s3_client,  # type: ignore[arg-type]
    )
    for i in range(4):
        await s_setup.write(f"k{i}", b"v")

    # Now wrap with a flaky client that fails the DELETE of the
    # oldest key (k0). Cap=2 wants to evict k0 and k1.
    flaky = _FlakyClient(s3_client, fail_on_key="ns/k0")
    s = BoundedS3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=flaky,  # type: ignore[arg-type]
        sink=sink,
        base_event_fields=base,
    )
    evicted = await s.evict_to_capacity(2)
    # k1 deleted, k0 failed -> reports 1 deletion.
    assert evicted == 1
    # Audit emitted exactly once, for k1 (the success).
    deletes = [e for e in sink.events if isinstance(e, MemoryDelete)]
    assert len(deletes) == 1
    assert deletes[0].key == "k1"
    # State on disk: k1 is gone (k0 failed delete -> still alive).
    keys = sorted(await s_setup.list_keys())
    assert "k0" in keys
    assert "k1" not in keys
    assert "k2" in keys
    assert "k3" in keys


@pytest.mark.asyncio
async def test_all_deletes_failing_emits_no_audit_and_returns_zero(
    s3_client: object,
) -> None:
    # If every DELETE in the batch fails (e.g., persistent S3 outage),
    # the function returns 0 and emits no audit events. This is the
    # symmetric case to the partial-failure path: keys stay alive,
    # no audit-vs-raise divergence, and the next TTLSweeper cycle
    # retries (the BL-199 sweeper-resilience contract).
    sink = MemorySink()
    base = {
        "workload": "w",
        "contract": "c",
        "contract_version": "1",
        "trace_id": "t",
        "span_id": "s",
    }

    class _AlwaysFailDelete:
        def __init__(self, real: object) -> None:
            self._real = real
            self.exceptions = real.exceptions  # type: ignore[attr-defined]

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

        def delete_object(self, **kw: object) -> object:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "SlowDown", "Message": "throttled"}},
                "DeleteObject",
            )

    s_setup = BoundedS3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=s3_client,  # type: ignore[arg-type]
    )
    for i in range(3):
        await s_setup.write(f"k{i}", b"v")

    flaky = _AlwaysFailDelete(s3_client)
    s = BoundedS3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=flaky,  # type: ignore[arg-type]
        sink=sink,
        base_event_fields=base,
    )
    evicted = await s.evict_to_capacity(1)
    assert evicted == 0
    assert [e for e in sink.events if isinstance(e, MemoryDelete)] == []
    # All three keys still alive.
    keys = sorted(await s_setup.list_keys())
    assert keys == ["k0", "k1", "k2"]


@pytest.mark.asyncio
async def test_happy_path_unchanged_by_per_item_containment(s3_client: object) -> None:
    # The per-item try/except must not regress the happy path:
    # 4 writes, cap=2, evicts 2, audits 2.
    sink = MemorySink()
    base = {
        "workload": "w",
        "contract": "c",
        "contract_version": "1",
        "trace_id": "t",
        "span_id": "s",
    }
    s = BoundedS3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=s3_client,  # type: ignore[arg-type]
        sink=sink,
        base_event_fields=base,
    )
    for i in range(4):
        await s.write(f"k{i}", b"v")
    evicted = await s.evict_to_capacity(2)
    assert evicted == 2
    deletes = [e for e in sink.events if isinstance(e, MemoryDelete)]
    assert len(deletes) == 2
    assert sorted(d.key for d in deletes) == ["k0", "k1"]


# ---- BL-227: base-exception propagation (BL-165 / BL-223 invariant) --------


@pytest.mark.asyncio
async def test_base_exception_still_propagates(s3_client: object) -> None:
    # The per-item containment catches ``Exception`` but NOT
    # ``BaseException``; ``KeyboardInterrupt`` / ``SystemExit`` /
    # ``asyncio.CancelledError`` must still propagate for the
    # BL-165 / BL-223 terminal-signal invariant. Use a client that
    # raises ``SystemExit`` (a BaseException) on delete; the
    # function must re-raise, not swallow.
    class _ExitOnDelete:
        def __init__(self, real: object) -> None:
            self._real = real
            self.exceptions = real.exceptions  # type: ignore[attr-defined]

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

        def delete_object(self, **kw: object) -> object:
            raise SystemExit("terminal")

    s_setup = BoundedS3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=s3_client,  # type: ignore[arg-type]
    )
    await s_setup.write("k0", b"v")
    await s_setup.write("k1", b"v")
    crashing = _ExitOnDelete(s3_client)
    s = BoundedS3Store(
        Namespace(name="ns", workload="w"),
        _BUCKET,
        client=crashing,  # type: ignore[arg-type]
    )
    with pytest.raises(SystemExit):
        await s.evict_to_capacity(1)
