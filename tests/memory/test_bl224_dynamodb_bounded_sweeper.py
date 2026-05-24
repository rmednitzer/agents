"""BL-224 (BL-135 size-bound on durable DynamoDB): BoundedDynamoDBStore.

Counterpart to BL-214's BoundedRedisStore tests, BL-213's SQLite
tests, and BL-212's InMemoryStore tests. Tests use ``moto`` (per the
existing ``tests/memory/test_dynamodb.py`` pattern) so the suite
stays offline and deterministic.

The DynamoDB case mirrors the Redis design: there is no native
insertion-order column, so the adapter stamps a per-namespace
monotonic ``seq`` Number attribute on every data item, allocated via
an atomic ``UpdateItem ADD seq :n`` on a per-namespace counter row at
``pk = "__evict_counter::<namespace>"``. The counter row is placed
outside the namespace's data prefix (``<namespace>::<key>``) so it
cannot collide with a user key and does not appear in
``list_keys`` / ``scan`` / ``sweep_expired`` results.

Tests focus on:

- ``BoundedDynamoDBStore`` satisfies the new Protocol (and the
  parent ``SweepableStore``); the bare ``DynamoDBStore`` does not,
  by design (opt-in subclass);
- ``evict_to_capacity`` evicts oldest-first by seq ascending, with
  a rewritten key shifting to *newest* (the BL-213-style
  overwrite-shifts-to-newest semantic, matching the SQLite /
  Redis divergence from BL-212's InMemoryStore first-write FIFO);
- the counter row does not leak into ``list_keys`` / ``scan`` /
  ``sweep_expired`` and does not collide with a user key named
  ``__evict_counter``;
- expired-but-unswept items are not counted toward the cap (the
  BL-195 read-vs-listing parity in DynamoDB form);
- audit emission per evicted key;
- TTLSweeper integration on a DynamoDB backend drives both the
  age-only sweep and the capacity pass;
- every keyspace-mutating path on the parent (write / mset /
  compare_and_set / write_versioned / transact) stamps a fresh
  seq so the eviction ordering stays consistent;
- the migration contract: a legacy item written without a seq
  attribute is treated as seq=0 (oldest) and evicts first;
- the counter source is monotonic across writes (no ties even on
  a tight loop) and one batch UpdateItem allocates one contiguous
  seq range per ``mset`` / ``transact``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from harness.events import MemoryDelete, MemoryWrite
from harness.sinks import MemorySink
from memory.store import BoundedSweepableStore, SweepableStore, TxnDelete, TxnWrite
from memory.sweep import TTLSweeper
from memory.types import Namespace

moto = pytest.importorskip("moto")
import boto3  # noqa: E402

from memory.dynamodb import BoundedDynamoDBStore, DynamoDBStore  # noqa: E402

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


def _store(client: object, name: str = "cap", **kw: object) -> BoundedDynamoDBStore:
    return BoundedDynamoDBStore(
        Namespace(name=name, workload="w"),
        _TABLE,
        client=client,  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


# ---- Protocol satisfaction -------------------------------------------------


def test_bounded_dynamodb_satisfies_bounded_sweepable(ddb_client: object) -> None:
    s = _store(ddb_client)
    assert isinstance(s, BoundedSweepableStore)
    assert isinstance(s, SweepableStore)


def test_bare_dynamodb_store_does_not_satisfy_bounded_sweepable(
    ddb_client: object,
) -> None:
    # Opt-in: the bare DynamoDBStore intentionally does not implement
    # BoundedSweepableStore, so a TTLSweeper(max_keys=...) load-time
    # isinstance check fails fast on a misconfigured wiring instead
    # of running with a no-op (parallel to BoundedRedisStore opt-in).
    s = DynamoDBStore(Namespace(name="cap", workload="w"), _TABLE, client=ddb_client)
    assert not isinstance(s, BoundedSweepableStore)
    # The bare DynamoDBStore *does* satisfy SweepableStore (it has
    # the parent's age-only sweep_expired); only the BOUNDED extension
    # is opt-in.
    assert isinstance(s, SweepableStore)


# ---- evict_to_capacity semantics -------------------------------------------


@pytest.mark.asyncio
async def test_evict_oldest_first_by_seq(ddb_client: object) -> None:
    # Tight loop with no sleeps: the server-side counter
    # ``UpdateItem ADD seq :one`` gives every write a unique
    # atomic seq, so this test would have failed under a
    # client-side ``time.time()`` scoring on a fast-enough host
    # (microsecond-resolution collisions). Pinned here so a
    # future score-source change surfaces in CI.
    s = _store(ddb_client)
    for i in range(5):
        await s.write(f"k{i}", str(i).encode())
    evicted = await s.evict_to_capacity(3)
    assert evicted == 2
    assert sorted(await s.list_keys()) == ["k2", "k3", "k4"]


@pytest.mark.asyncio
async def test_rewrite_shifts_key_to_newest(ddb_client: object) -> None:
    # The DynamoDB divergence (parallel to BL-213's SQLite and
    # BL-214's Redis): a re-PutItem of an existing key allocates a
    # fresh seq from the counter and replaces the whole item, so
    # the rewritten key orders as *newest* by seq. Diverges from
    # the InMemoryStore first-write FIFO. Pinned by test.
    s = _store(ddb_client)
    await s.write("a", b"1")
    await s.write("b", b"2")
    await s.write("c", b"3")
    await s.write("a", b"refreshed")  # bumps a's seq
    await s.evict_to_capacity(2)
    # Oldest by seq after the rewrite: b, c, a; cap 2 evicts b,
    # leaving c and a.
    assert sorted(await s.list_keys()) == ["a", "c"]


@pytest.mark.asyncio
async def test_evict_noop_when_under_cap(ddb_client: object) -> None:
    s = _store(ddb_client)
    await s.write("a", b"1")
    await s.write("b", b"2")
    assert await s.evict_to_capacity(5) == 0
    assert sorted(await s.list_keys()) == ["a", "b"]


@pytest.mark.asyncio
async def test_evict_noop_when_exact(ddb_client: object) -> None:
    s = _store(ddb_client)
    await s.write("a", b"1")
    await s.write("b", b"2")
    assert await s.evict_to_capacity(2) == 0
    assert sorted(await s.list_keys()) == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, -100])
async def test_evict_rejects_non_positive(ddb_client: object, bad: int) -> None:
    s = _store(ddb_client)
    with pytest.raises(ValueError, match="positive"):
        await s.evict_to_capacity(bad)


# ---- Counter row isolation -------------------------------------------------


@pytest.mark.asyncio
async def test_counter_does_not_leak_into_list_keys(ddb_client: object) -> None:
    # The counter row lives outside the namespace prefix
    # (``__evict_counter::<namespace>``), so it cannot match the
    # ``begins_with(pk, "<namespace>::")`` filter used by list_keys.
    s = _store(ddb_client)
    await s.write("k1", b"v")
    keys = await s.list_keys()
    assert keys == ["k1"]


@pytest.mark.asyncio
async def test_counter_does_not_leak_into_scan(ddb_client: object) -> None:
    s = _store(ddb_client)
    await s.write("k1", b"v")
    seen: list[str] = []
    cursor = ""
    while True:
        cursor, page = await s.scan(cursor=cursor, count=100)
        seen.extend(page)
        if not cursor:
            break
    assert sorted(seen) == ["k1"]


@pytest.mark.asyncio
async def test_user_key_named_evict_counter_does_not_collide(
    ddb_client: object,
) -> None:
    # A user-written key named ``__evict_counter`` would, under a
    # naive design, alias the internal counter row. The adapter
    # places the counter outside the namespace prefix so the user
    # key's pk is ``<namespace>::__evict_counter`` while the
    # counter's pk is ``__evict_counter::<namespace>`` (different
    # strings, no collision possible).
    s = _store(ddb_client)
    await s.write("__evict_counter", b"user-data")
    assert await s.read("__evict_counter") == b"user-data"
    # And the user key shows up in list_keys; the auxiliary
    # counter itself does not.
    keys = await s.list_keys()
    assert keys == ["__evict_counter"]


@pytest.mark.asyncio
async def test_sweep_expired_does_not_touch_counter(ddb_client: object) -> None:
    # The age-only sweep scans the namespace prefix; the counter
    # row is outside that prefix so it's never touched by sweep.
    s = _store(ddb_client)
    await s.write("k", b"v")  # advances counter to 1
    # The counter row exists (we can read it directly via raw boto3)
    counter_pk = f"__evict_counter::{s.namespace.name}"
    before = ddb_client.get_item(  # type: ignore[attr-defined]
        TableName=_TABLE, Key={"pk": {"S": counter_pk}}
    )
    assert "Item" in before
    # Now run sweep; the counter row must still exist.
    await s.sweep_expired()
    after = ddb_client.get_item(  # type: ignore[attr-defined]
        TableName=_TABLE, Key={"pk": {"S": counter_pk}}
    )
    assert "Item" in after
    assert after["Item"]["seq"]["N"] == before["Item"]["seq"]["N"]


# ---- evict + expired items (BL-195 parity) ---------------------------------


@pytest.mark.asyncio
async def test_evict_skips_expired_items(ddb_client: object) -> None:
    # A TTL'd item past its expiry (DynamoDB's native sweep lags so
    # the row is still present until ``sweep_expired`` or a read
    # removes it) must not count toward the cap; the BL-195
    # read-vs-listing parity in DynamoDB form. Otherwise the cap
    # would double-evict a live key while the dead row still
    # occupied the nominal slot.
    s = _store(ddb_client)
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
    # alive1 is gone; alive2 and alive3 remain; ``dead`` is still
    # in the table but read() treats it as absent (lazy expiry).
    assert sorted(await s.list_keys()) == ["alive2", "alive3"]


# ---- Audit emission --------------------------------------------------------


@pytest.mark.asyncio
async def test_evict_emits_audit_per_key(ddb_client: object) -> None:
    sink = MemorySink()
    s = BoundedDynamoDBStore(
        Namespace(name="cap", workload="w"),
        _TABLE,
        client=ddb_client,  # type: ignore[arg-type]
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
async def test_ttl_sweeper_drives_capacity_pass(ddb_client: object) -> None:
    s = _store(ddb_client)
    for i in range(4):
        await s.write(f"k{i}", b"v")
    async with TTLSweeper(s, interval_seconds=0.01, max_keys=2) as sweeper:
        await asyncio.sleep(0.1)
    assert sweeper.evicted_total >= 2
    assert sorted(await s.list_keys()) == ["k2", "k3"]


@pytest.mark.asyncio
async def test_ttl_sweeper_both_passes(ddb_client: object) -> None:
    # 1 expiring + 3 non-expiring above a cap of 2. The expiring
    # one is dropped by sweep_expired (age-only pass), then the
    # capacity pass evicts the oldest of the three live items to
    # land at the cap.
    s = _store(ddb_client)
    await s.write("dies", b"v", ttl_seconds=0.02)
    await s.write("a", b"v")
    await s.write("b", b"v")
    await s.write("c", b"v")
    await asyncio.sleep(0.05)
    async with TTLSweeper(s, interval_seconds=0.01, max_keys=2) as sweeper:
        await asyncio.sleep(0.1)
    assert sweeper.swept_total >= 1
    assert sweeper.evicted_total >= 1
    assert len(await s.list_keys()) == 2


# ---- seq consistency across every mutation path ----------------------------


@pytest.mark.asyncio
async def test_seq_tracks_mset_mdelete(ddb_client: object) -> None:
    s = _store(ddb_client)
    await s.mset({"a": b"1", "b": b"2", "c": b"3"})
    await s.mdelete(["b"])
    # Cap=1 must evict "a" specifically (not "c"): the
    # ``UpdateItem ADD`` counter assigns a contiguous monotonic
    # range to each mset batch in dict iteration order, so "a"
    # gets the lowest seq and is the oldest.
    assert await s.evict_to_capacity(1) == 1
    assert sorted(await s.list_keys()) == ["c"]


@pytest.mark.asyncio
async def test_seq_tracks_compare_and_set(ddb_client: object) -> None:
    s = _store(ddb_client)
    await s.write("k", b"v0")
    ok = await s.compare_and_set("k", b"v0", b"v1")
    assert ok
    # CAS-updated key should still be in the eviction pool; cap=1
    # is a no-op since only one key is present.
    assert await s.evict_to_capacity(1) == 0


@pytest.mark.asyncio
async def test_seq_tracks_compare_and_delete(ddb_client: object) -> None:
    s = _store(ddb_client)
    await s.write("k", b"v")
    ok = await s.compare_and_delete("k", b"v")
    assert ok
    # The eviction pool should now be empty; cap=1 is a no-op.
    assert await s.evict_to_capacity(1) == 0
    assert await s.read("k") is None


@pytest.mark.asyncio
async def test_seq_tracks_versioned_write_and_delete(ddb_client: object) -> None:
    s = _store(ddb_client)
    token1 = await s.write_versioned("k", b"v1")
    assert token1 is not None
    token2 = await s.write_versioned("k", b"v2", expected_version=token1)
    assert token2 is not None
    ok = await s.delete_versioned("k", token2)
    assert ok
    assert await s.evict_to_capacity(1) == 0
    assert await s.read("k") is None


@pytest.mark.asyncio
async def test_seq_tracks_transact(ddb_client: object) -> None:
    s = _store(ddb_client)
    # First write so we have a version token to delete with.
    t_a = await s.write_versioned("a", b"1")
    assert t_a is not None
    # Transact: write b/c, delete a.
    out = await s.transact(
        writes={"b": TxnWrite(value=b"2"), "c": TxnWrite(value=b"3")},
        deletes={"a": TxnDelete(expected_version=t_a)},
    )
    assert out is not None
    assert sorted(await s.list_keys()) == ["b", "c"]
    # Eviction pool has b and c; cap=1 evicts the older.
    assert await s.evict_to_capacity(1) == 1
    assert len(await s.list_keys()) == 1


# ---- Monotonic seq source --------------------------------------------------


@pytest.mark.asyncio
async def test_seqs_are_server_side_monotonic_not_client_wallclock(
    ddb_client: object,
) -> None:
    # The DynamoDB counterpart of the BL-214 Redis test: pin that
    # the seq source is a server-side monotonic counter, not
    # client wall-clock. Read the counter row directly: each
    # single write advances seq by one, and an mset of N keys
    # advances it by exactly N in one UpdateItem (one round trip
    # per batch).
    s = _store(ddb_client)
    counter_pk = f"__evict_counter::{s.namespace.name}"
    for i in range(5):
        await s.write(f"k{i}", b"v")
    resp = ddb_client.get_item(  # type: ignore[attr-defined]
        TableName=_TABLE, Key={"pk": {"S": counter_pk}}
    )
    assert int(resp["Item"]["seq"]["N"]) == 5
    # mset of three more advances the counter by exactly three in
    # one UpdateItem.
    await s.mset({"x": b"1", "y": b"2", "z": b"3"})
    resp = ddb_client.get_item(  # type: ignore[attr-defined]
        TableName=_TABLE, Key={"pk": {"S": counter_pk}}
    )
    assert int(resp["Item"]["seq"]["N"]) == 8


@pytest.mark.asyncio
async def test_strict_fifo_across_tight_loop(ddb_client: object) -> None:
    # Tight loop with no sleeps would, under client-side
    # time.time() scoring, often produce ties on a fast host.
    # Under the server-side counter this is strict insertion
    # order. Write keys in a known NOT-sorted order so a lex
    # tie-break would give a different eviction result than the
    # FIFO contract.
    s = _store(ddb_client)
    insertion_order = ["zulu", "alpha", "mike", "bravo", "papa"]
    for k in insertion_order:
        await s.write(k, b"v")
    # Evict the three oldest; under FIFO that is [zulu, alpha,
    # mike], leaving [bravo, papa]. Under a broken tie-break it
    # would be different.
    evicted = await s.evict_to_capacity(2)
    assert evicted == 3
    assert sorted(await s.list_keys()) == ["bravo", "papa"]


@pytest.mark.asyncio
async def test_mset_preserves_dict_insertion_order_under_fifo(
    ddb_client: object,
) -> None:
    # mset allocates seqs via a single UpdateItem ADD then zips
    # them to items.items() in dict iteration order (insertion
    # order on Python 3.7+).
    s = _store(ddb_client)
    # Caller writes z, a, m in that intended FIFO order. A broken
    # tie-break would evict by lex (a first) before z (insertion
    # first).
    await s.mset({"z": b"1", "a": b"2", "m": b"3"})
    evicted = await s.evict_to_capacity(1)  # keep one (the newest, m)
    assert evicted == 2
    assert sorted(await s.list_keys()) == ["m"]


@pytest.mark.asyncio
async def test_transact_writes_preserve_batch_fifo_order(ddb_client: object) -> None:
    # transact() writes go through one UpdateItem ADD allocating
    # ``len(writes)`` contiguous seqs assigned in dict iteration
    # order. The contract: a transactional batch is internally
    # FIFO by the caller's dict order, not by member name.
    s = _store(ddb_client)
    out = await s.transact(
        writes={
            "z_first": TxnWrite(value=b"1"),
            "a_second": TxnWrite(value=b"2"),
            "m_third": TxnWrite(value=b"3"),
        }
    )
    assert out is not None
    # Evict 2: should remove z_first and a_second (the two oldest
    # by insertion); m_third stays.
    evicted = await s.evict_to_capacity(1)
    assert evicted == 2
    assert sorted(await s.list_keys()) == ["m_third"]


# ---- Migration: legacy items without seq -----------------------------------


@pytest.mark.asyncio
async def test_legacy_items_without_seq_evict_first(ddb_client: object) -> None:
    # A pre-existing item written via a bare DynamoDBStore has no
    # ``seq`` attribute. The eviction logic treats such items as
    # seq=0 (oldest) so they evict first. A subsequent write via
    # BoundedDynamoDBStore stamps a fresh seq and the key moves to
    # the newest position. Migration contract; same shape as the
    # LIMITATIONS.md L17 ``ver`` migration.
    bare = DynamoDBStore(Namespace(name="cap", workload="w"), _TABLE, client=ddb_client)
    await bare.write("legacy1", b"1")
    await bare.write("legacy2", b"2")
    # Now switch to the bounded subclass and write fresh entries.
    s = _store(ddb_client)
    await s.write("fresh1", b"3")
    await s.write("fresh2", b"4")
    # Cap=2: evicts the two legacy items first (seq=0 tie-broken
    # by key ascending so legacy1 then legacy2).
    evicted = await s.evict_to_capacity(2)
    assert evicted == 2
    assert sorted(await s.list_keys()) == ["fresh1", "fresh2"]


@pytest.mark.asyncio
async def test_rewriting_a_legacy_item_stamps_seq(ddb_client: object) -> None:
    # After rewriting a legacy item via the bounded subclass, it
    # gets a fresh seq and is no longer evicted first.
    bare = DynamoDBStore(Namespace(name="cap", workload="w"), _TABLE, client=ddb_client)
    await bare.write("legacy", b"1")
    s = _store(ddb_client)
    await s.write("fresh1", b"2")
    # Rewrite the legacy via the bounded subclass: it gets a
    # fresh seq, moving to the newest position.
    await s.write("legacy", b"updated")
    await s.write("fresh2", b"3")
    # Now order by seq is: fresh1 (1) < legacy (2) < fresh2 (3).
    # Cap=2 evicts fresh1 only.
    evicted = await s.evict_to_capacity(2)
    assert evicted == 1
    assert sorted(await s.list_keys()) == ["fresh2", "legacy"]


# ---- mset empty short-circuit ----------------------------------------------


@pytest.mark.asyncio
async def test_mset_empty_is_noop(ddb_client: object) -> None:
    # Empty-batch short-circuit (BL-198 class extension): no
    # counter UpdateItem, no batch_write. Parity with the BL-178
    # SQLiteStore fix and the BL-198 RedisStore fix.
    s = _store(ddb_client)
    counter_pk = f"__evict_counter::{s.namespace.name}"
    # The counter row should not exist yet.
    before = ddb_client.get_item(  # type: ignore[attr-defined]
        TableName=_TABLE, Key={"pk": {"S": counter_pk}}
    )
    assert "Item" not in before
    await s.mset({})
    # The counter row still should not exist (no UpdateItem made).
    after = ddb_client.get_item(  # type: ignore[attr-defined]
        TableName=_TABLE, Key={"pk": {"S": counter_pk}}
    )
    assert "Item" not in after
