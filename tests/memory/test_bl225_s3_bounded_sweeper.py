"""BL-225 (BL-135 size-bound on durable S3): BoundedS3Store.

Counterpart to BL-214's BoundedRedisStore tests, BL-213's SQLite tests,
BL-212's InMemoryStore tests, and BL-224's DynamoDB tests. Tests use
``moto`` (per the existing ``tests/memory/test_s3.py`` pattern) so the
suite stays offline and deterministic.

The S3 case is structurally different from Redis/DynamoDB: there is no
server-side atomic counter primitive, so the adapter stamps an
``insertion-order`` user-metadata attribute on every PUT, set to
``time.time_ns()`` at write time. Eviction LISTs the namespace prefix,
HEADs each object to read ``insertion-order`` / ``expires-at``,
filters expired-but-unswept, sorts by ``(insertion-order, key)`` ASC,
and DELETEs the oldest ``overflow``.

Tests focus on:

- ``BoundedS3Store`` satisfies the new Protocol (and the parent
  ``SweepableStore``); the bare ``S3Store`` does not, by design
  (opt-in subclass);
- ``evict_to_capacity`` evicts oldest-first by insertion-order ascending,
  with a rewritten key shifting to *newest* (the BL-213-style
  overwrite-shifts-to-newest semantic, matching the SQLite / Redis /
  DynamoDB divergence from BL-212's InMemoryStore first-write FIFO);
- expired-but-unswept items are not counted toward the cap (the
  BL-195 read-vs-listing parity in S3 form);
- audit emission per evicted key;
- TTLSweeper integration on an S3 backend drives both the age-only
  sweep and the capacity pass;
- the migration contract: a legacy object written without
  ``insertion-order`` metadata is treated as ``seq = 0`` (oldest) and
  evicts first;
- ``mset`` preserves dict insertion order under FIFO (the per-write
  ``time.time_ns()`` stamp advances monotonically inside a single
  process).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator

import pytest

from harness.events import MemoryDelete, MemoryWrite
from harness.sinks import MemorySink
from memory.store import BoundedSweepableStore, SweepableStore
from memory.sweep import TTLSweeper
from memory.types import Namespace

moto = pytest.importorskip("moto")
import boto3  # noqa: E402

from memory.s3 import BoundedS3Store, S3Store  # noqa: E402

_BUCKET = "test-bucket"


@pytest.fixture
def s3_client() -> Iterator[object]:
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield client


def _store(client: object, name: str = "cap", **kw: object) -> BoundedS3Store:
    return BoundedS3Store(
        Namespace(name=name, workload="w"),
        _BUCKET,
        client=client,  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


async def _wait_until(
    predicate: Callable[[], bool], *, attempts: int = 20, delay: float = 0.01
) -> None:
    for _ in range(attempts):
        await asyncio.sleep(delay)
        if predicate():
            break


# ---- Protocol satisfaction -------------------------------------------------


def test_bounded_s3_satisfies_bounded_sweepable(s3_client: object) -> None:
    s = _store(s3_client)
    assert isinstance(s, BoundedSweepableStore)
    assert isinstance(s, SweepableStore)


def test_bare_s3_store_does_not_satisfy_bounded_sweepable(s3_client: object) -> None:
    # Opt-in: the bare S3Store intentionally does not implement
    # BoundedSweepableStore, so a TTLSweeper(max_keys=...) load-time
    # isinstance check fails fast on a misconfigured wiring instead
    # of running with a no-op (parallel to BoundedRedisStore /
    # BoundedDynamoDBStore opt-in).
    s = S3Store(Namespace(name="cap", workload="w"), _BUCKET, client=s3_client)
    assert not isinstance(s, BoundedSweepableStore)
    # The bare S3Store *does* satisfy SweepableStore (it has the
    # parent's age-only sweep_expired); only the BOUNDED extension is
    # opt-in.
    assert isinstance(s, SweepableStore)


# ---- evict_to_capacity semantics -------------------------------------------


@pytest.mark.asyncio
async def test_evict_oldest_first_by_insertion_order(s3_client: object) -> None:
    # time.time_ns() advances monotonically within a single process on
    # any platform whose clock has nanosecond resolution (Linux /
    # macOS), so a tight loop of writes orders strictly by insertion
    # under the single-writer contract. The cross-writer divergence
    # is the documented S3-specific trade-off.
    s = _store(s3_client)
    for i in range(5):
        await s.write(f"k{i}", str(i).encode())
    evicted = await s.evict_to_capacity(3)
    assert evicted == 2
    assert sorted(await s.list_keys()) == ["k2", "k3", "k4"]


@pytest.mark.asyncio
async def test_rewrite_shifts_key_to_newest(s3_client: object) -> None:
    # The S3 divergence (parallel to BL-213's SQLite, BL-214's Redis,
    # BL-224's DynamoDB): a re-PutObject of an existing key stamps a
    # fresh insertion-order and overwrites the previous metadata, so
    # the rewritten key orders as *newest*. Diverges from the
    # InMemoryStore first-write FIFO. Pinned by test.
    s = _store(s3_client)
    await s.write("a", b"1")
    await s.write("b", b"2")
    await s.write("c", b"3")
    await s.write("a", b"refreshed")  # bumps a's insertion-order
    await s.evict_to_capacity(2)
    # Oldest by insertion-order after the rewrite: b, c, a; cap 2
    # evicts b, leaving c and a.
    assert sorted(await s.list_keys()) == ["a", "c"]


@pytest.mark.asyncio
async def test_evict_noop_when_under_cap(s3_client: object) -> None:
    s = _store(s3_client)
    await s.write("a", b"1")
    await s.write("b", b"2")
    assert await s.evict_to_capacity(5) == 0
    assert sorted(await s.list_keys()) == ["a", "b"]


@pytest.mark.asyncio
async def test_evict_noop_when_exact(s3_client: object) -> None:
    s = _store(s3_client)
    await s.write("a", b"1")
    await s.write("b", b"2")
    assert await s.evict_to_capacity(2) == 0
    assert sorted(await s.list_keys()) == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, -100])
async def test_evict_rejects_non_positive(s3_client: object, bad: int) -> None:
    s = _store(s3_client)
    with pytest.raises(ValueError, match="positive"):
        await s.evict_to_capacity(bad)


# ---- evict + expired items (BL-195 parity) ---------------------------------


@pytest.mark.asyncio
async def test_evict_skips_expired_items(s3_client: object) -> None:
    # An item past its TTL (S3 has no native object-level expiry,
    # so the object stays until ``sweep_expired`` or a read removes
    # it) must not count toward the cap; the BL-195 read-vs-listing
    # parity in S3 form. Otherwise the cap would double-evict a live
    # key while the dead object still occupied the nominal slot.
    s = _store(s3_client)
    await s.write("alive1", b"1")
    await s.write("dead", b"2", ttl_seconds=0.02)
    await s.write("alive2", b"3")
    await s.write("alive3", b"4")
    await asyncio.sleep(0.05)
    # Three live (alive1, alive2, alive3), one expired (dead).
    # Cap 2 should evict exactly the oldest live (alive1) without
    # double-evicting anything.
    evicted = await s.evict_to_capacity(2)
    assert evicted == 1
    # alive1 is gone; alive2 and alive3 remain.
    assert sorted(await s.list_keys()) == ["alive2", "alive3"]


# ---- Audit emission --------------------------------------------------------


@pytest.mark.asyncio
async def test_evict_emits_audit_per_key(s3_client: object) -> None:
    sink = MemorySink()
    s = BoundedS3Store(
        Namespace(name="cap", workload="w"),
        _BUCKET,
        client=s3_client,  # type: ignore[arg-type]
        sink=sink,
        base_event_fields={
            "workload": "w",
            "contract": "c",
            "contract_version": "1",
            "trace_id": "t",
            "span_id": "s",
        },
    )
    for i in range(4):
        await s.write(f"k{i}", b"v")
    await s.evict_to_capacity(2)
    writes = [e for e in sink.events if isinstance(e, MemoryWrite)]
    deletes = [e for e in sink.events if isinstance(e, MemoryDelete)]
    assert len(writes) == 4
    assert len(deletes) == 2


# ---- TTLSweeper integration ------------------------------------------------


@pytest.mark.asyncio
async def test_ttl_sweeper_drives_capacity_pass(s3_client: object) -> None:
    s = _store(s3_client)
    for i in range(4):
        await s.write(f"k{i}", b"v")
    async with TTLSweeper(s, interval_seconds=0.01, max_keys=2) as sweeper:
        await _wait_until(lambda: sweeper.evicted_total >= 2)
    assert sweeper.evicted_total >= 2
    assert sorted(await s.list_keys()) == ["k2", "k3"]


@pytest.mark.asyncio
async def test_ttl_sweeper_both_passes(s3_client: object) -> None:
    # 1 expiring + 3 non-expiring above a cap of 2. The expiring
    # one is dropped by sweep_expired (age-only pass), then the
    # capacity pass evicts the oldest of the three live items to
    # land at the cap.
    s = _store(s3_client)
    await s.write("dies", b"v", ttl_seconds=0.02)
    await s.write("a", b"v")
    await s.write("b", b"v")
    await s.write("c", b"v")
    await asyncio.sleep(0.05)
    async with TTLSweeper(s, interval_seconds=0.01, max_keys=2) as sweeper:
        await _wait_until(lambda: sweeper.swept_total >= 1 and sweeper.evicted_total >= 1)
    assert sweeper.swept_total >= 1
    assert sweeper.evicted_total >= 1
    assert len(await s.list_keys()) == 2


# ---- insertion-order consistency across mset / write_content ---------------


@pytest.mark.asyncio
async def test_mset_preserves_dict_insertion_order_under_fifo(
    s3_client: object,
) -> None:
    # The bare S3Store.mset is implemented as a loop of self.write,
    # so the overridden write() stamps a fresh insertion-order per
    # item in dict iteration order (insertion order on Python 3.7+).
    # A caller writing z, a, m in that intended FIFO order expects
    # z to be the oldest; under a broken design that sorted by lex
    # tie-break first, a would be evicted before z.
    s = _store(s3_client)
    await s.mset({"z": b"1", "a": b"2", "m": b"3"})
    evicted = await s.evict_to_capacity(1)  # keep one (the newest, m)
    assert evicted == 2
    assert sorted(await s.list_keys()) == ["m"]


@pytest.mark.asyncio
async def test_write_content_carries_insertion_order(s3_client: object) -> None:
    # write_content delegates to self.write (parent's
    # implementation), so the override stamps insertion-order on
    # content-addressed PUTs too. Pin that the size-bound applies
    # to write_content as a sanity check.
    s = _store(s3_client)
    k1 = await s.write_content(b"older")
    k2 = await s.write_content(b"middle")
    k3 = await s.write_content(b"newer")
    await s.evict_to_capacity(2)
    remaining = sorted(await s.list_keys())
    # The oldest content key is evicted; the two newest remain.
    assert k1 not in remaining
    assert k2 in remaining
    assert k3 in remaining


# ---- Migration: legacy items without insertion-order -----------------------


@pytest.mark.asyncio
async def test_legacy_items_without_insertion_order_evict_first(
    s3_client: object,
) -> None:
    # A pre-existing object written via a bare S3Store has no
    # insertion-order metadata. The eviction logic treats such
    # objects as insertion-order=0 (oldest) so they evict first.
    # A subsequent write via BoundedS3Store stamps a fresh
    # insertion-order and the key moves to the newest position.
    # Migration contract; same shape as BoundedDynamoDBStore (BL-224)
    # for ``seq``.
    bare = S3Store(Namespace(name="cap", workload="w"), _BUCKET, client=s3_client)
    await bare.write("legacy1", b"1")
    await bare.write("legacy2", b"2")
    # Now switch to the bounded subclass and write fresh entries.
    s = _store(s3_client)
    await s.write("fresh1", b"3")
    await s.write("fresh2", b"4")
    # Cap=2: evicts the two legacy items first (seq=0 tie-broken by
    # key ascending so legacy1 then legacy2).
    evicted = await s.evict_to_capacity(2)
    assert evicted == 2
    assert sorted(await s.list_keys()) == ["fresh1", "fresh2"]


@pytest.mark.asyncio
async def test_rewriting_a_legacy_item_stamps_insertion_order(
    s3_client: object,
) -> None:
    # After rewriting a legacy item via the bounded subclass, it
    # gets a fresh insertion-order and is no longer evicted first.
    bare = S3Store(Namespace(name="cap", workload="w"), _BUCKET, client=s3_client)
    await bare.write("legacy", b"1")
    s = _store(s3_client)
    await s.write("fresh1", b"2")
    # Rewrite the legacy via the bounded subclass: it gets a fresh
    # insertion-order, moving to the newest position.
    await s.write("legacy", b"updated")
    await s.write("fresh2", b"3")
    # Now order by insertion-order is: fresh1 < legacy < fresh2.
    # Cap=2 evicts fresh1 only.
    evicted = await s.evict_to_capacity(2)
    assert evicted == 1
    assert sorted(await s.list_keys()) == ["fresh2", "legacy"]


# ---- Namespace isolation ---------------------------------------------------


@pytest.mark.asyncio
async def test_eviction_respects_namespace_prefix(s3_client: object) -> None:
    # Two BoundedS3Stores share the same bucket via distinct
    # namespaces (and therefore distinct prefixes). evict_to_capacity
    # in one namespace must not touch objects in the other.
    a = _store(s3_client, name="ns_a")
    b = _store(s3_client, name="ns_b")
    for i in range(3):
        await a.write(f"k{i}", b"v")
    for i in range(3):
        await b.write(f"k{i}", b"v")
    await a.evict_to_capacity(1)
    # a is capped at 1 entry; b is untouched.
    assert len(await a.list_keys()) == 1
    assert len(await b.list_keys()) == 3


# ---- Backward-compatibility on parent surface -------------------------------


@pytest.mark.asyncio
async def test_read_write_delete_roundtrip(s3_client: object) -> None:
    # Parent surface unchanged: write / read / delete still work
    # byte-for-byte the same; the new insertion-order metadata is
    # transparent to the reader.
    s = _store(s3_client)
    await s.write("k", b"\x00bin\xff")
    assert await s.read("k") == b"\x00bin\xff"
    await s.delete("k")
    assert await s.read("k") is None


@pytest.mark.asyncio
async def test_ttl_still_works(s3_client: object) -> None:
    # The two metadata stamps (insertion-order + expires-at)
    # coexist; lazy expiry on read still fires correctly.
    s = _store(s3_client)
    await s.write("t", b"v", ttl_seconds=0.05)
    await asyncio.sleep(0.1)
    assert await s.read("t") is None


@pytest.mark.asyncio
async def test_list_keys_unchanged(s3_client: object) -> None:
    # The override does not change list_keys behavior. A pre-existing
    # legacy item and a fresh BoundedS3Store item both surface; only
    # the eviction order differs.
    bare = S3Store(Namespace(name="cap", workload="w"), _BUCKET, client=s3_client)
    await bare.write("legacy", b"v")
    s = _store(s3_client)
    await s.write("fresh", b"v")
    assert sorted(await s.list_keys()) == ["fresh", "legacy"]


# ---- wrap_acl / wrap_encrypted forwarding (BL-156) -------------------------


@pytest.mark.asyncio
async def test_wrap_acl_forwards_bounded_protocol(s3_client: object) -> None:
    from memory.acl import RoleACL, wrap_acl

    inner = _store(s3_client)
    policy = RoleACL(
        roles={"p": "admin"},
        grants={"admin": {"read", "write", "delete", "list"}},
    )
    wrapped = wrap_acl(inner, policy, principal="p")
    assert isinstance(wrapped, BoundedSweepableStore)
    for i in range(3):
        await inner.write(f"k{i}", b"v")
    # The call routes through the mixin to the inner store.
    assert await wrapped.evict_to_capacity(1) == 2  # type: ignore[attr-defined]
    assert len(await inner.list_keys()) == 1


@pytest.mark.asyncio
async def test_wrap_encrypted_forwards_bounded_protocol(s3_client: object) -> None:
    crypto = pytest.importorskip("cryptography")  # noqa: F841
    from memory.encryption import StaticKeyProvider, wrap_encrypted

    inner = _store(s3_client)
    wrapped = wrap_encrypted(inner, StaticKeyProvider(b"k" * 32))
    assert isinstance(wrapped, BoundedSweepableStore)
    # Write encrypted entries, then evict the overflow.
    for i in range(3):
        await wrapped.write(f"k{i}", b"plaintext")
    assert await wrapped.evict_to_capacity(1) == 2  # type: ignore[attr-defined]
    assert len(await inner.list_keys()) == 1
