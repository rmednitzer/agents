"""BL-235 (BL-135 tiering half): TieredMemoryStore hot/cold composition.

Tests cover:

- construction: distinct stores required, namespace name/workload must
  match (retention may differ per tier), the wrapper satisfies the L1
  ``MemoryStore`` Protocol;
- read: hot hit, cold fall-through, miss, promotion on/off, the CAS
  no-clobber guarantee against a racing hot write, the plain-write
  fallback on a CAS-less hot tier, promoted copies carrying the hot
  namespace default TTL;
- write: hot-first plus cold invalidation by default, the
  ``invalidate_cold_on_write=False`` opt-out keeping the cold copy
  shadowed;
- delete: removes both tiers, idempotent on absent keys;
- list_keys: sorted union with prefix filtering;
- demote: moves live values cold-ward, version-gated hot delete (a
  concurrently rewritten key stays hot and is not counted), plain
  path on a non-versioned hot tier, TTL passthrough, all-or-nothing
  key validation, absent keys skipped;
- demote_to_capacity: first-write wrapper order (overwrite keeps the
  slot, BL-212 semantics), unknown keys first with lexicographic ties
  (the BL-224/BL-225 sentinel-0 contract), promotion stamping recency,
  no-op at or under the cap, non-positive cap rejected;
- review hardening: the wrapper validates keys on its own L1 surface,
  a failed CAS promotion does not stamp, a failing cold invalidation
  does not strip the stamp of a landed hot write, a lost demote race
  (rewrite or delete) leaves no stale cold ghost, and the capacity
  prune keeps the stamp of a write landing during the listing await.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from memory.errors import NamespaceViolation
from memory.inmemory import InMemoryStore
from memory.store import MemoryStore
from memory.tiering import TieredMemoryStore
from memory.types import Namespace


def _ns(retention_seconds: float | None = None) -> Namespace:
    return Namespace(name="tier", workload="w", retention_seconds=retention_seconds)


def _tiered(
    *,
    hot_retention: float | None = None,
    **kwargs: bool,
) -> tuple[TieredMemoryStore, InMemoryStore, InMemoryStore]:
    hot = InMemoryStore(_ns(hot_retention))
    cold = InMemoryStore(_ns())
    return TieredMemoryStore(hot, cold, **kwargs), hot, cold


class _CoreOnlyStore:
    """L1-only hot tier double: no CAS, no version tokens."""

    name = "core-only"

    def __init__(self) -> None:
        self.namespace = _ns()
        self._data: dict[str, bytes] = {}

    async def read(self, key: str) -> bytes | None:
        return self._data.get(key)

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        self._data[key] = value

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._data if k.startswith(prefix))


class _HookedStore:
    """InMemoryStore wrapper firing one-shot hooks before read/write.

    Used as the cold tier to model a concurrent hot writer landing
    while the wrapper is suspended at a cold-tier await (the
    interleaving a real race would use).
    """

    name = "hooked"

    def __init__(self, inner: InMemoryStore) -> None:
        self._inner = inner
        self.before_read: Callable[[], Awaitable[None]] | None = None
        self.before_write: Callable[[], Awaitable[None]] | None = None

    @property
    def namespace(self) -> Namespace:
        return self._inner.namespace

    async def read(self, key: str) -> bytes | None:
        hook, self.before_read = self.before_read, None
        if hook is not None:
            await hook()
        return await self._inner.read(key)

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        hook, self.before_write = self.before_write, None
        if hook is not None:
            await hook()
        await self._inner.write(key, value, ttl_seconds=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._inner.delete(key)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return await self._inner.list_keys(prefix)


# ---- construction -----------------------------------------------------------


def test_rejects_same_store_for_both_tiers() -> None:
    s = InMemoryStore(_ns())
    with pytest.raises(ValueError, match="distinct"):
        TieredMemoryStore(s, s)


def test_rejects_namespace_name_mismatch() -> None:
    hot = InMemoryStore(Namespace(name="tier", workload="w"))
    cold = InMemoryStore(Namespace(name="other", workload="w"))
    with pytest.raises(ValueError, match="share namespace"):
        TieredMemoryStore(hot, cold)


def test_rejects_workload_mismatch() -> None:
    hot = InMemoryStore(Namespace(name="tier", workload="w"))
    cold = InMemoryStore(Namespace(name="tier", workload="other"))
    with pytest.raises(ValueError, match="share namespace"):
        TieredMemoryStore(hot, cold)


def test_retention_may_differ_per_tier() -> None:
    t, hot, _ = _tiered(hot_retention=60.0)
    assert t.namespace is hot.namespace
    assert t.name == "tiered"


def test_satisfies_memory_store_protocol() -> None:
    t, _, _ = _tiered()
    assert isinstance(t, MemoryStore)


# ---- read -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_serves_hot_hit() -> None:
    t, hot, cold = _tiered()
    await hot.write("k", b"hot")
    await cold.write("k", b"cold")
    assert await t.read("k") == b"hot"


@pytest.mark.asyncio
async def test_read_falls_through_to_cold_and_promotes() -> None:
    t, hot, cold = _tiered()
    await cold.write("k", b"v")
    assert await t.read("k") == b"v"
    assert await hot.read("k") == b"v"  # promoted
    assert await cold.read("k") == b"v"  # cold copy retained


@pytest.mark.asyncio
async def test_read_miss_returns_none() -> None:
    t, _, _ = _tiered()
    assert await t.read("missing") is None


@pytest.mark.asyncio
async def test_promotion_disabled_leaves_hot_untouched() -> None:
    t, hot, cold = _tiered(promote_on_read=False)
    await cold.write("k", b"v")
    assert await t.read("k") == b"v"
    assert await hot.read("k") is None


@pytest.mark.asyncio
async def test_promotion_does_not_clobber_racing_hot_write() -> None:
    hot = InMemoryStore(_ns())
    cold = _HookedStore(InMemoryStore(_ns()))
    t = TieredMemoryStore(hot, cold)
    await cold.write("k", b"cold-old")

    async def racing_hot_write() -> None:
        await hot.write("k", b"hot-new")

    cold.before_read = racing_hot_write
    # The fall-through read returns the cold value it found, but the
    # CAS promotion (expected absent) loses to the newer hot write.
    assert await t.read("k") == b"cold-old"
    assert await hot.read("k") == b"hot-new"
    assert await t.read("k") == b"hot-new"


@pytest.mark.asyncio
async def test_promotion_plain_write_on_cas_less_hot_tier() -> None:
    hot = _CoreOnlyStore()
    cold = InMemoryStore(_ns())
    t = TieredMemoryStore(hot, cold)
    await cold.write("k", b"v")
    assert await t.read("k") == b"v"
    assert await hot.read("k") == b"v"


@pytest.mark.asyncio
async def test_promoted_copy_carries_hot_default_ttl() -> None:
    t, hot, _ = _tiered(hot_retention=0.02)
    await t.write("k", b"v")  # lands hot with the 0.02 s default
    # Push it cold so the next read promotes a fresh hot copy.
    assert await t.demote(["k"]) == 1
    assert await t.read("k") == b"v"
    assert await hot.read("k") == b"v"
    await asyncio.sleep(0.05)
    assert await hot.read("k") is None  # the promoted copy expired hot
    assert await t.read("k") == b"v"  # still served (and re-promoted) from cold


# ---- write ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_lands_hot_and_invalidates_cold() -> None:
    t, hot, cold = _tiered()
    await cold.write("k", b"old")
    await t.write("k", b"new")
    assert await hot.read("k") == b"new"
    assert await cold.read("k") is None
    assert await t.read("k") == b"new"


@pytest.mark.asyncio
async def test_write_invalidation_opt_out_keeps_shadowed_cold_copy() -> None:
    t, hot, cold = _tiered(invalidate_cold_on_write=False)
    await cold.write("k", b"old")
    await t.write("k", b"new")
    assert await hot.read("k") == b"new"
    assert await cold.read("k") == b"old"  # kept; shadowed by hot
    assert await t.read("k") == b"new"


class _FailingDeleteStore(_HookedStore):
    """Cold tier double whose delete raises for selected keys."""

    def __init__(self, inner: InMemoryStore, fail_keys: set[str]) -> None:
        super().__init__(inner)
        self.fail_keys = fail_keys

    async def delete(self, key: str) -> None:
        if key in self.fail_keys:
            raise RuntimeError("transient cold delete failure")
        await super().delete(key)


@pytest.mark.asyncio
async def test_write_stamps_before_cold_invalidation_failure() -> None:
    # The hot write landed, so a failing cold invalidation must not
    # strip the key's write-order slot: an unstamped live key would
    # rank legacy-oldest and be the next demote_to_capacity victim.
    hot = InMemoryStore(_ns())
    cold = _FailingDeleteStore(InMemoryStore(_ns()), {"k"})
    t = TieredMemoryStore(hot, cold)
    with pytest.raises(RuntimeError, match="cold delete"):
        await t.write("k", b"v")
    assert await hot.read("k") == b"v"  # the hot write stuck
    await hot.write("z", b"v")  # direct hot write: legacy sentinel
    cold.fail_keys.clear()
    # The sentinel key demotes first; the stamped "k" stays hot.
    assert await t.demote_to_capacity(1) == 1
    assert await hot.list_keys() == ["k"]


# ---- delete -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_both_tiers() -> None:
    t, hot, cold = _tiered(invalidate_cold_on_write=False)
    await t.write("k", b"v")
    await cold.write("k", b"stale")
    await t.delete("k")
    assert await hot.read("k") is None
    assert await cold.read("k") is None
    assert await t.read("k") is None


@pytest.mark.asyncio
async def test_delete_is_idempotent_on_absent_key() -> None:
    t, _, _ = _tiered()
    await t.delete("missing")  # no raise


# ---- list_keys --------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_keys_is_sorted_union_with_prefix() -> None:
    t, hot, cold = _tiered()
    await hot.write("a1", b"v")
    await hot.write("both", b"v")
    await cold.write("b1", b"v")
    await cold.write("both", b"v")
    assert await t.list_keys() == ["a1", "b1", "both"]
    assert await t.list_keys(prefix="b") == ["b1", "both"]


# ---- demote -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_demote_moves_live_value_cold_ward() -> None:
    t, hot, cold = _tiered()
    await t.write("k", b"v")
    assert await t.demote(["k"]) == 1
    assert await hot.read("k") is None
    assert await cold.read("k") == b"v"
    assert await t.read("k") == b"v"


@pytest.mark.asyncio
async def test_demote_skips_absent_keys() -> None:
    t, _, cold = _tiered()
    assert await t.demote(["missing"]) == 0
    assert await cold.read("missing") is None


@pytest.mark.asyncio
async def test_demote_ttl_applies_to_cold_copy() -> None:
    t, _, cold = _tiered()
    await t.write("k", b"v")
    assert await t.demote(["k"], ttl_seconds=0.02) == 1
    assert await cold.read("k") == b"v"
    await asyncio.sleep(0.05)
    assert await cold.read("k") is None


@pytest.mark.asyncio
async def test_demote_validates_all_keys_before_mutation() -> None:
    t, hot, cold = _tiered()
    await t.write("ok", b"v")
    with pytest.raises(NamespaceViolation):
        await t.demote(["ok", "bad key"])
    assert await hot.read("ok") == b"v"  # untouched
    assert await cold.read("ok") is None


@pytest.mark.asyncio
async def test_demote_concurrent_hot_rewrite_keeps_key_hot() -> None:
    hot = InMemoryStore(_ns())
    cold = _HookedStore(InMemoryStore(_ns()))
    t = TieredMemoryStore(hot, cold)
    await t.write("k", b"v1")

    async def racing_hot_rewrite() -> None:
        await hot.write("k", b"v2")

    cold.before_write = racing_hot_rewrite
    # The version-gated hot delete loses to the rewrite: not counted,
    # the newer hot value survives, and the just-written stale cold
    # copy is removed again (no ghost to resurface after hot expiry).
    assert await t.demote(["k"]) == 0
    assert await hot.read("k") == b"v2"
    assert await cold.read("k") is None
    assert await t.read("k") == b"v2"


@pytest.mark.asyncio
async def test_demote_concurrent_delete_leaves_no_cold_ghost() -> None:
    hot = InMemoryStore(_ns())
    cold = _HookedStore(InMemoryStore(_ns()))
    t = TieredMemoryStore(hot, cold)
    await t.write("k", b"v")

    async def racing_delete() -> None:
        await hot.delete("k")

    cold.before_write = racing_delete
    # The hot copy is deleted between the versioned read and the
    # version-gated delete: the lost race must also remove the cold
    # copy demote just wrote, or the deleted key would resurrect on
    # the next fall-through read.
    assert await t.demote(["k"]) == 0
    assert await cold.read("k") is None
    assert await t.read("k") is None


@pytest.mark.asyncio
async def test_demote_failed_undo_does_not_strand_remaining_keys() -> None:
    # The lost-race cold undo is contained per key (the BL-233
    # idempotent-DELETE convention): a transient failure of the undo
    # must not abort the demotion of the keys after it.
    hot = InMemoryStore(_ns())
    cold = _FailingDeleteStore(InMemoryStore(_ns()), {"a"})
    t = TieredMemoryStore(hot, cold, invalidate_cold_on_write=False)
    await t.write("a", b"v1")
    await t.write("b", b"v2")

    async def racing_rewrite() -> None:
        await hot.write("a", b"v1-newer")

    cold.before_write = racing_rewrite
    # "a" loses its versioned race and its cold undo raises; "b" is
    # still demoted.
    assert await t.demote(["a", "b"]) == 1
    assert await hot.read("a") == b"v1-newer"  # stayed hot
    assert await hot.read("b") is None
    assert await cold.read("b") == b"v2"


@pytest.mark.asyncio
async def test_demote_plain_path_on_non_versioned_hot_tier() -> None:
    hot = _CoreOnlyStore()
    cold = InMemoryStore(_ns())
    t = TieredMemoryStore(hot, cold)
    await t.write("k", b"v")
    assert await t.demote(["k"]) == 1
    assert await hot.read("k") is None
    assert await cold.read("k") == b"v"


# ---- demote_to_capacity ------------------------------------------------------


@pytest.mark.asyncio
async def test_capacity_demotes_first_write_order() -> None:
    t, hot, cold = _tiered()
    for i in range(1, 6):
        await t.write(f"k{i}", b"v")
    assert await t.demote_to_capacity(3) == 2
    assert await hot.list_keys() == ["k3", "k4", "k5"]
    assert await cold.list_keys() == ["k1", "k2"]


@pytest.mark.asyncio
async def test_capacity_overwrite_keeps_original_slot() -> None:
    # BL-212 insertion-order semantics: rewriting "a" does not refresh
    # its slot, so it is still the oldest and demotes first, carrying
    # the latest value with it.
    t, hot, cold = _tiered()
    await t.write("a", b"1")
    await t.write("b", b"2")
    await t.write("c", b"3")
    await t.write("a", b"rewritten")
    assert await t.demote_to_capacity(2) == 1
    assert await hot.list_keys() == ["b", "c"]
    assert await cold.read("a") == b"rewritten"


@pytest.mark.asyncio
async def test_capacity_demotes_unknown_keys_first_lexicographically() -> None:
    # Keys written directly to the hot store (never through the
    # wrapper) carry the BL-224 legacy sentinel 0: oldest, demoted
    # first, ties broken lexicographically.
    t, hot, cold = _tiered()
    await hot.write("x", b"v")
    await hot.write("a", b"v")
    await t.write("w1", b"v")
    await t.write("w2", b"v")
    assert await t.demote_to_capacity(2) == 2
    assert await hot.list_keys() == ["w1", "w2"]
    assert await cold.list_keys() == ["a", "x"]


@pytest.mark.asyncio
async def test_capacity_promotion_stamps_recency() -> None:
    t, hot, cold = _tiered()
    await cold.write("p", b"v")  # cold-only
    await hot.write("z", b"v")  # hot-direct: unknown to the wrapper
    assert await t.read("p") == b"v"  # promotes p and stamps it
    # If promotion did not stamp, "p" and "z" would both rank at the
    # sentinel and "p" would demote first lexicographically.
    assert await t.demote_to_capacity(1) == 1
    assert await hot.read("z") is None
    assert await hot.read("p") == b"v"


@pytest.mark.asyncio
async def test_failed_cas_promotion_does_not_stamp() -> None:
    # A promotion that lost its CAS race did not insert anything: the
    # slot is owned by a direct hot write the wrapper never made, so
    # the key must keep the legacy sentinel and demote first.
    hot = InMemoryStore(_ns())
    cold = _HookedStore(InMemoryStore(_ns()))
    t = TieredMemoryStore(hot, cold)
    await cold.write("k", b"cold-old")

    async def racing_hot_write() -> None:
        await hot.write("k", b"hot-new")

    cold.before_read = racing_hot_write
    assert await t.read("k") == b"cold-old"  # CAS promotion lost
    await t.write("w", b"v")  # wrapper-stamped, newer
    assert await t.demote_to_capacity(1) == 1
    assert await hot.list_keys() == ["w"]
    assert await cold.read("k") == b"hot-new"  # demoted hot value


class _ListHookedHotStore(_CoreOnlyStore):
    """Hot tier double firing a one-shot hook after list_keys snapshots."""

    def __init__(self) -> None:
        super().__init__()
        self.after_list: Callable[[], Awaitable[None]] | None = None

    async def list_keys(self, prefix: str = "") -> list[str]:
        keys = await super().list_keys(prefix)
        hook, self.after_list = self.after_list, None
        if hook is not None:
            await hook()
        return keys


@pytest.mark.asyncio
async def test_capacity_prune_keeps_stamp_of_concurrent_write() -> None:
    # A write landing while demote_to_capacity awaits list_keys stamps
    # a key the listing snapshot does not include; the prune must not
    # drop that stamp (the key would rank legacy-oldest on the next
    # pass despite being the newest write).
    hot = _ListHookedHotStore()
    cold = InMemoryStore(_ns())
    t = TieredMemoryStore(hot, cold)
    for key in ("k1", "k2", "k3"):
        await t.write(key, b"v")

    async def concurrent_write() -> None:
        await t.write("zz", b"v")

    hot.after_list = concurrent_write
    assert await t.demote_to_capacity(4) == 0  # under cap; prune runs
    assert await t.demote_to_capacity(3) == 1
    assert await hot.list_keys() == ["k2", "k3", "zz"]  # k1 demoted, not zz


@pytest.mark.asyncio
async def test_capacity_noop_at_or_under_cap() -> None:
    t, hot, _ = _tiered()
    await t.write("a", b"v")
    await t.write("b", b"v")
    assert await t.demote_to_capacity(2) == 0
    assert await t.demote_to_capacity(5) == 0
    assert await hot.list_keys() == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1])
async def test_capacity_rejects_non_positive_cap(bad: int) -> None:
    t, _, _ = _tiered()
    with pytest.raises(ValueError, match="positive"):
        await t.demote_to_capacity(bad)


# ---- key validation on the L1 surface ----------------------------------------


@pytest.mark.asyncio
async def test_l1_surface_validates_keys() -> None:
    # The wrapper enforces the MemoryStore key contract itself (the
    # ACLStore decorator precedent), so validation does not depend on
    # the inner tier implementations.
    t, hot, cold = _tiered()
    with pytest.raises(NamespaceViolation):
        await t.read("bad key")
    with pytest.raises(NamespaceViolation):
        await t.write("bad key", b"v")
    with pytest.raises(NamespaceViolation):
        await t.delete("bad key")
    assert await hot.list_keys() == []
    assert await cold.list_keys() == []
