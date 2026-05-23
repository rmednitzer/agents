"""BL-212 (BL-135 size-bound half): BoundedSweepableStore + TTLSweeper(max_keys=...).

The size-bound is additive to age-only sweep. Tests cover:

- the new Protocol satisfies on InMemoryStore (and not on a sweep-only
  reference);
- ``evict_to_capacity`` removes oldest-first when over, no-op when under
  or at the cap, validates the cap, and the cap-exact case is treated
  as no-op (boundary parity with ``sweep_expired`` returning 0 on a
  clean store);
- expired entries are not counted toward the cap (they are sweepable
  on the next age-only pass, so the capacity pass treats them as
  already gone);
- the sweeper wires the capacity pass after sweep on each interval,
  surfaces ``evicted_total`` separately from ``swept_total``, and
  fails fast at construction on a non-bounded store or a non-positive
  cap;
- forwarding through ``wrap_acl`` and ``wrap_encrypted`` keeps
  ``isinstance(BoundedSweepableStore)`` truthful and routes the call
  to the inner store.
"""

from __future__ import annotations

import asyncio

import pytest

from harness.events import MemoryDelete, MemoryWrite
from harness.sinks import MemorySink
from memory.acl import RoleACL, wrap_acl
from memory.encryption import StaticKeyProvider, wrap_encrypted
from memory.inmemory import InMemoryStore
from memory.store import BoundedSweepableStore, SweepableStore
from memory.sweep import TTLSweeper
from memory.types import Namespace


def _store(**kwargs: object) -> InMemoryStore:
    return InMemoryStore(Namespace(name="cap", workload="w"), **kwargs)  # type: ignore[arg-type]


def _allow_all_policy() -> RoleACL:
    return RoleACL(
        roles={"p": "admin"},
        grants={"admin": {"read", "write", "delete", "list"}},
    )


# ---- Protocol satisfaction -------------------------------------------------


def test_inmemory_satisfies_bounded_sweepable() -> None:
    s = _store()
    assert isinstance(s, BoundedSweepableStore)
    # BL-156: the broader Protocol still holds.
    assert isinstance(s, SweepableStore)


# ---- evict_to_capacity semantics -------------------------------------------


@pytest.mark.asyncio
async def test_evict_oldest_first_until_under_cap() -> None:
    s = _store()
    for i in range(5):
        await s.write(f"k{i}", str(i).encode())
    evicted = await s.evict_to_capacity(3)
    assert evicted == 2
    assert sorted(await s.list_keys()) == ["k2", "k3", "k4"]


@pytest.mark.asyncio
async def test_overwrite_does_not_change_insertion_order() -> None:
    # Python dict semantics: re-writing an existing key keeps its
    # original position. The Protocol docs make this explicit; the
    # test pins it so a future change of mind triggers a CI failure.
    s = _store()
    await s.write("a", b"1")
    await s.write("b", b"2")
    await s.write("c", b"3")
    await s.write("a", b"overwritten")  # keeps a's slot at index 0
    await s.evict_to_capacity(2)
    assert sorted(await s.list_keys()) == ["b", "c"]


@pytest.mark.asyncio
async def test_evict_to_capacity_is_noop_when_under_cap() -> None:
    s = _store()
    await s.write("a", b"1")
    await s.write("b", b"2")
    assert await s.evict_to_capacity(5) == 0
    assert sorted(await s.list_keys()) == ["a", "b"]


@pytest.mark.asyncio
async def test_evict_to_capacity_is_noop_when_exact() -> None:
    s = _store()
    await s.write("a", b"1")
    await s.write("b", b"2")
    assert await s.evict_to_capacity(2) == 0
    assert sorted(await s.list_keys()) == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, -100])
async def test_evict_to_capacity_rejects_non_positive(bad: int) -> None:
    s = _store()
    with pytest.raises(ValueError, match="positive"):
        await s.evict_to_capacity(bad)


@pytest.mark.asyncio
async def test_evict_skips_expired_entries() -> None:
    # An expired-but-unswept entry must not count toward the cap;
    # ``sweep_expired`` (which the sweeper runs first) is the path that
    # removes those, and counting them here would double-evict live
    # entries while the dead ones still occupy the dict.
    s = _store()
    await s.write("alive1", b"1")
    await s.write("dead", b"2", ttl_seconds=0.02)
    await s.write("alive2", b"3")
    await s.write("alive3", b"4")
    await asyncio.sleep(0.05)
    # Live keys: alive1, alive2, alive3 (3 entries). Cap at 2 should
    # evict exactly 1, the oldest live one.
    evicted = await s.evict_to_capacity(2)
    assert evicted == 1
    # Remaining live keys exclude alive1 (oldest live, evicted) and
    # exclude dead (expired, dropped on read).
    remaining = sorted(await s.list_keys())
    assert remaining == ["alive2", "alive3"]


@pytest.mark.asyncio
async def test_evict_audits_each_deletion() -> None:
    # BL-040: a write-then-evict cycle on an audited store emits a
    # MemoryDelete per evicted key, so an operator can attribute the
    # space reclamation to the capacity pass.
    sink = MemorySink()
    s = InMemoryStore(
        Namespace(name="cap", workload="w"),
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
    # 4 writes + 2 deletes.
    assert len(writes) == 4
    assert len(deletes) == 2


# ---- TTLSweeper integration ------------------------------------------------


@pytest.mark.asyncio
async def test_sweeper_drives_capacity_pass_after_age_sweep() -> None:
    s = _store()
    # 4 entries with no TTL: age-sweep removes nothing; capacity pass
    # removes the oldest two.
    for i in range(4):
        await s.write(f"k{i}", b"v")
    async with TTLSweeper(s, interval_seconds=0.01, max_keys=2) as sweeper:
        await asyncio.sleep(0.05)
    assert sweeper.swept_total == 0  # nothing TTL-expired
    assert sweeper.evicted_total >= 2  # capacity-evicted the overflow
    assert sorted(await s.list_keys()) == ["k2", "k3"]


@pytest.mark.asyncio
async def test_sweeper_uses_both_passes_on_one_interval() -> None:
    s = _store()
    # 1 expiring, 3 non-expiring above a cap of 2: sweep removes the
    # expired one; the remaining 3 live entries trigger the capacity
    # pass to evict 1 to land at the cap.
    await s.write("dies", b"v", ttl_seconds=0.02)
    await s.write("a", b"v")
    await s.write("b", b"v")
    await s.write("c", b"v")
    await asyncio.sleep(0.05)
    async with TTLSweeper(s, interval_seconds=0.01, max_keys=2) as sweeper:
        await asyncio.sleep(0.05)
    assert sweeper.swept_total >= 1
    assert sweeper.evicted_total >= 1
    # Live keys after both passes land at the cap.
    assert len(await s.list_keys()) == 2


@pytest.mark.asyncio
async def test_sweeper_rejects_non_bounded_store_with_max_keys() -> None:
    # A pure SweepableStore (no evict_to_capacity) cannot be wired with
    # max_keys; surface the configuration error at load time, not
    # mid-run (ADR 0007).
    class SweepOnly:
        async def sweep_expired(self) -> int:
            return 0

    with pytest.raises(TypeError, match="BoundedSweepableStore"):
        TTLSweeper(SweepOnly(), interval_seconds=1, max_keys=10)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1])
async def test_sweeper_rejects_non_positive_max_keys(bad: int) -> None:
    s = _store()
    with pytest.raises(ValueError, match="max_keys must be positive"):
        TTLSweeper(s, interval_seconds=1, max_keys=bad)


@pytest.mark.asyncio
async def test_sweeper_without_max_keys_preserves_legacy_behaviour() -> None:
    # max_keys default None preserves the BL-080 / BL-199 surface
    # byte-for-byte (additive-to-L1, ADR 0007). No evict call should
    # happen even on a bounded store; evicted_total stays 0.
    s = _store()
    for i in range(5):
        await s.write(f"k{i}", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    async with TTLSweeper(s, interval_seconds=0.01) as sweeper:
        await asyncio.sleep(0.05)
    assert sweeper.swept_total >= 5
    assert sweeper.evicted_total == 0


# ---- wrap_acl / wrap_encrypted forwarding (BL-156) -------------------------


@pytest.mark.asyncio
async def test_wrap_acl_forwards_bounded_protocol() -> None:
    inner = _store()
    policy = _allow_all_policy()
    wrapped = wrap_acl(inner, policy, principal="p")
    assert isinstance(wrapped, BoundedSweepableStore)
    for i in range(3):
        await inner.write(f"k{i}", b"v")
    # The call routes through the mixin to the inner store.
    assert await wrapped.evict_to_capacity(1) == 2  # type: ignore[attr-defined]
    assert len(await inner.list_keys()) == 1


@pytest.mark.asyncio
async def test_wrap_acl_does_not_advertise_bounded_on_sweep_only_inner() -> None:
    # A sweep-only store wrapped by ACL gets _ACLSweepMixin, not
    # _ACLBoundedMixin; isinstance(BoundedSweepableStore) stays False
    # so the sweeper's load-time check catches a mis-wiring.
    class SweepOnly:
        namespace = Namespace(name="cap", workload="w")

        async def sweep_expired(self) -> int:
            return 0

        async def read(self, key: str) -> bytes | None:
            return None

        async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
            return None

        async def delete(self, key: str) -> None:
            return None

        async def list_keys(self, *, prefix: str = "") -> list[str]:
            return []

    inner = SweepOnly()
    wrapped = wrap_acl(inner, _allow_all_policy(), principal="p")  # type: ignore[arg-type]
    assert isinstance(wrapped, SweepableStore)
    assert not isinstance(wrapped, BoundedSweepableStore)


@pytest.mark.asyncio
async def test_wrap_encrypted_forwards_bounded_protocol() -> None:
    inner = _store()
    wrapped = wrap_encrypted(inner, StaticKeyProvider(b"k" * 32))
    assert isinstance(wrapped, BoundedSweepableStore)
    # Write encrypted entries, then evict the overflow.
    for i in range(3):
        await wrapped.write(f"k{i}", b"plaintext")
    assert await wrapped.evict_to_capacity(1) == 2  # type: ignore[attr-defined]
    assert len(await inner.list_keys()) == 1
