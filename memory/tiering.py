"""Hot/cold two-tier memory composition (BL-235, BL-135 close).

Long-horizon workloads keep a small working set hot (fast, often
size-capped, short retention) and a long tail cold (durable, cheap,
long retention). ``TieredMemoryStore`` composes two namespace-matched
``MemoryStore`` instances behind the plain ``MemoryStore`` surface
(the ``InMemorySemanticStore`` composition pattern, BL-131): reads
fall through hot to cold and optionally promote, writes land hot,
``demote`` / ``demote_to_capacity`` move overflow down. It is a
wrapper over existing Protocols, not a new store Protocol: no adapter
changes, nothing to fake (ADR 0004).

Consistency posture:

- ``write`` lands in hot first, then invalidates the cold copy (by
  default). Hot-first means a crash in between leaves both copies
  present and ``read`` serves the newer hot one; the invalidation
  prevents an older cold copy resurfacing after the hot copy expires.
  ``invalidate_cold_on_write=False`` skips the cold round-trip per
  write (a performance opt-out) and accepts exactly that resurfacing
  window for keys that are rewritten and then expire hot. The same
  window opens when the invalidation itself raises: the hot write has
  landed (and is stamped), so a caller that sees the exception should
  retry the write (or delete the key) to close it.
- ``delete`` removes cold first, then hot. Cold-first means a failure
  in between leaves the hot copy live (still consistent, retry
  deletes it); hot-first would leave only the stale cold copy and a
  fall-through read would resurrect deleted data.
- Promotion (cold hit copied up) uses ``compare_and_set(key, None,
  value)`` when the hot tier implements ``CASMemoryStore``, so a
  concurrent hot write is never clobbered by the older cold value;
  on a CAS-less hot tier it degrades to a plain write under the
  single-writer-per-key posture (BL-224/BL-225).
- ``demote`` on a ``VersionedMemoryStore`` hot tier deletes the hot
  copy only if its version token is unchanged since the read
  (``delete_versioned``); a concurrently rewritten or deleted key
  stays as-is and is not counted, and the just-written cold copy is
  removed again so the lost race leaves no stale cold ghost behind.

Demotion order (BL-224/BL-225 contract): ``demote_to_capacity`` ranks
hot keys by the wrapper's own write sequence, first-write order, an
overwrite keeping the original slot (the BL-212 insertion-order
semantics; LRU stays out of tree, LIMITATIONS L5). Keys the wrapper
has never written (pre-existing hot entries, direct inner-store
writes) carry the legacy sentinel 0: oldest, demoted first, ties
broken lexicographically.

TTLs: each tier applies its own namespace default. The two namespaces
must agree on ``name`` and ``workload`` (checked at construction, so
a mis-wiring surfaces at load time, ADR 0007) but may differ in
``retention_seconds``: short-lived hot, long-lived cold is the point
of tiering. A promoted copy gets the hot tier's default TTL; a
demoted copy gets the cold tier's default unless ``ttl_seconds`` is
given.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from memory.store import CASMemoryStore, MemoryStore, VersionedMemoryStore
from memory.types import Namespace
from memory.validators import validate_key

__all__ = ["TieredMemoryStore"]


class TieredMemoryStore:
    """Two-tier hot/cold MemoryStore composition (BL-235).

    Implements the L1 ``MemoryStore`` surface plus the demotion
    helpers. Extension Protocols of the inner tiers are intentionally
    not forwarded: a batch read or a transaction spanning two tiers
    has no single-store semantics to inherit (ADR 0004 "don't fake
    it"); callers needing them hold the inner store directly.

    Usage::

        hot = InMemoryStore(Namespace(name="ctx", workload="w",
                                      retention_seconds=3600))
        cold = SQLiteStore(Namespace(name="ctx", workload="w"), path=...)
        store = TieredMemoryStore(hot, cold)
        ...
        await store.demote_to_capacity(max_hot_keys=512)
    """

    name: str = "tiered"

    def __init__(
        self,
        hot: MemoryStore,
        cold: MemoryStore,
        *,
        promote_on_read: bool = True,
        invalidate_cold_on_write: bool = True,
    ) -> None:
        if hot is cold:
            raise ValueError("hot and cold must be distinct stores")
        if (
            hot.namespace.name != cold.namespace.name
            or hot.namespace.workload != cold.namespace.workload
        ):
            raise ValueError(
                "hot and cold tiers must share namespace name and workload; got "
                f"{hot.namespace.name!r}/{hot.namespace.workload!r} and "
                f"{cold.namespace.name!r}/{cold.namespace.workload!r} "
                "(retention_seconds may differ per tier)"
            )
        self._hot = hot
        self._cold = cold
        self._promote_on_read = promote_on_read
        self._invalidate_cold_on_write = invalidate_cold_on_write
        # Resolve the hot tier's optional capabilities once (the
        # TTLSweeper capacity-pass idiom); the casts at the use sites
        # avoid re-narrowing per call.
        self._hot_is_cas = isinstance(hot, CASMemoryStore)
        self._hot_is_versioned = isinstance(hot, VersionedMemoryStore)
        # Wrapper-write sequence for demote_to_capacity ordering.
        # First-write order; an overwrite keeps the original slot
        # (BL-212 semantics). Unknown keys default to the BL-224
        # legacy sentinel 0 at ranking time. The map is process-local:
        # after a restart every pre-existing hot key reverts to the
        # sentinel until the wrapper writes it again.
        self._order: dict[str, int] = {}
        self._seq = 0

    @property
    def namespace(self) -> Namespace:
        return self._hot.namespace

    def _stamp(self, key: str) -> None:
        if key not in self._order:
            self._seq += 1
            self._order[key] = self._seq

    # --- MemoryStore (L1) ----------------------------------------------

    async def read(self, key: str) -> bytes | None:
        validate_key(key)
        value = await self._hot.read(key)
        if value is not None:
            return value
        value = await self._cold.read(key)
        if value is None:
            return None
        if self._promote_on_read:
            if self._hot_is_cas:
                # Only promote into an absent slot: a hot write that
                # raced this read is newer than the cold copy and must
                # win. The promoted copy gets the hot default TTL.
                # Stamp only when the CAS landed: a lost race means a
                # direct hot write owns the slot, and a key the wrapper
                # never wrote keeps the legacy sentinel (BL-224/BL-225).
                promoted = await cast(CASMemoryStore, self._hot).compare_and_set(key, None, value)
            else:
                await self._hot.write(key, value)
                promoted = True
            if promoted:
                # Promotion is an insertion into hot; stamp it so the
                # capacity pass does not treat a just-promoted key as
                # legacy-oldest.
                self._stamp(key)
        return value

    async def write(
        self,
        key: str,
        value: bytes,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        validate_key(key)
        # Hot first: a failure after the hot write leaves a stale cold
        # copy that the newer hot value shadows on read. Stamp before
        # the cold round-trip: the hot write succeeded, so a failing
        # cold invalidation must not strip the key's write-order slot
        # (an unstamped live key would rank legacy-oldest and become
        # the next demote_to_capacity victim).
        await self._hot.write(key, value, ttl_seconds=ttl_seconds)
        self._stamp(key)
        if self._invalidate_cold_on_write:
            await self._cold.delete(key)

    async def delete(self, key: str) -> None:
        validate_key(key)
        # Cold first: hot-first would leave only the stale cold copy,
        # and a fall-through read would resurrect deleted data.
        await self._cold.delete(key)
        await self._hot.delete(key)
        self._order.pop(key, None)

    async def list_keys(self, prefix: str = "") -> list[str]:
        hot_keys = await self._hot.list_keys(prefix)
        cold_keys = await self._cold.list_keys(prefix)
        return sorted(set(hot_keys) | set(cold_keys))

    # --- demotion --------------------------------------------------------

    async def demote(
        self,
        keys: Sequence[str],
        *,
        ttl_seconds: float | None = None,
    ) -> int:
        """Move the live hot values of ``keys`` down to the cold tier.

        Copies each live hot value to cold (``ttl_seconds`` of ``None``
        falls back to the cold namespace default), then deletes the hot
        copy. On a ``VersionedMemoryStore`` hot tier the delete is
        version-gated: a key rewritten or deleted between read and
        delete stays as-is and is not counted, and the just-written
        cold copy is removed again so a lost race leaves no stale cold
        entry behind (a ghost would resurface after the hot copy
        expires, or resurrect a concurrently deleted key). Returns the
        number of keys actually moved. Keys are validated up front
        (all-or-nothing, the BatchMemoryStore convention); absent and
        expired keys are skipped.
        """
        ordered = list(dict.fromkeys(keys))
        for key in ordered:
            validate_key(key)
        moved = 0
        if self._hot_is_versioned:
            hot = cast(VersionedMemoryStore, self._hot)
            for key in ordered:
                hit = await hot.read_versioned(key)
                if hit is None:
                    continue
                value, token = hit
                await self._cold.write(key, value, ttl_seconds=ttl_seconds)
                if await hot.delete_versioned(key, token):
                    moved += 1
                    self._order.pop(key, None)
                else:
                    # Lost the race: the hot copy was rewritten (the
                    # newer value must not be shadowed by our stale
                    # cold write after it expires) or deleted (a cold
                    # ghost would resurrect it). Undo the cold copy,
                    # contained per key (the BL-233 idempotent-DELETE
                    # convention) so a transient failure of the undo
                    # does not strand the remaining keys; the leftover
                    # copy is overwritten or removed by the key's next
                    # demotion, invalidation, or delete.
                    try:
                        await self._cold.delete(key)
                    except Exception:
                        continue
        else:
            # CAS-less hot tier: read-copy-delete with the documented
            # lost-update window, acceptable only under the
            # single-writer-per-key posture (BL-224/BL-225).
            for key in ordered:
                raw = await self._hot.read(key)
                if raw is None:
                    continue
                await self._cold.write(key, raw, ttl_seconds=ttl_seconds)
                await self._hot.delete(key)
                moved += 1
                self._order.pop(key, None)
        return moved

    async def demote_to_capacity(
        self,
        max_hot_keys: int,
        *,
        ttl_seconds: float | None = None,
    ) -> int:
        """Demote the oldest hot keys until at most ``max_hot_keys`` remain.

        The ``evict_to_capacity`` shape (BL-212) with demotion instead
        of loss: overflow moves to cold rather than being dropped.
        Ranking is the wrapper write sequence with unknown keys first
        (sentinel 0, ties lexicographic; the BL-224/BL-225 legacy
        contract). Returns the number of keys moved; a no-op (0) when
        the live hot keyspace is at or under the cap. Under contention
        a version-gated demotion can move fewer keys than the overflow
        (a rewritten key stays hot, see ``demote``), so the hot tier
        may still exceed the cap when this returns; the periodic caller
        re-converges on the next pass. ``max_hot_keys`` must be
        positive (zero would empty the hot tier; call ``demote`` with
        every key if that is the intent).
        """
        if max_hot_keys <= 0:
            raise ValueError("max_hot_keys must be positive")
        # Snapshot the stamped keys before the await: a write that
        # lands during list_keys stamps a key that the listing below
        # does not include, and pruning it would demote a just-written
        # key as legacy-oldest.
        known = set(self._order)
        live = await self._hot.list_keys()
        live_set = set(live)
        # Prune stamps for keys that left hot through expiry, demotion,
        # or direct inner-store deletes, so the map stays bounded by
        # the live hot keyspace.
        for stale in known - live_set:
            self._order.pop(stale, None)
        overflow = len(live) - max_hot_keys
        if overflow <= 0:
            return 0
        ranked = sorted(live, key=lambda k: (self._order.get(k, 0), k))
        return await self.demote(ranked[:overflow], ttl_seconds=ttl_seconds)
