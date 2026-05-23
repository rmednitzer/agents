"""Sixth-audit memory fixes: regression tests for `BL-197` / `BL-198` /
`BL-199` (ADR 0015).

`BL-197` (Namespace TTL validation + resolve_ttl consolidation): the
`Namespace.retention_seconds` check accepted NaN and +inf because
`nan <= 0` and `inf <= 0` are both False; the BL-195 helpers then
treated such an `expires_at` as never-expired, silently disabling
expiration. The constructor now rejects non-finite values; the new
`Namespace.resolve_ttl` method merges the five-way `_ttl` /
`_effective_ttl` duplication and applies the same finite-and-positive
validation to per-call ``ttl_seconds``.

`BL-198` (RedisStore.mset empty-batch short-circuit): parity with the
BL-178 SQLite fix and with RedisStore.mdelete; an empty `items` no
longer round-trips a Redis pipeline.

`BL-199` (TTLSweeper failure resilience): a transient
`sweep_expired` exception used to propagate out of the background
loop and kill the sweeper silently; the loop now catches the
exception, records it on `failures_total` / `last_error`, and retries
at the next interval.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from memory.inmemory import InMemoryStore
from memory.store import SweepableStore
from memory.sweep import TTLSweeper
from memory.types import Namespace

# --- BL-197: Namespace TTL validation + resolve_ttl ------------------


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_namespace_rejects_non_finite_retention(bad: float) -> None:
    """NaN and infinities are rejected at construction.

    Pre-`BL-197` the `retention_seconds <= 0` check passed NaN
    (because `nan <= 0` is False) and the BL-195 helpers treated the
    resulting `expires_at = now + NaN` as never-expired, silently
    disabling expiration. The fix surfaces the bug at the API
    boundary (Copilot review on PR #51).
    """
    with pytest.raises(ValueError, match="finite"):
        Namespace(name="ns", workload="w", retention_seconds=bad)


def test_namespace_rejects_non_positive_retention() -> None:
    """The original positive-only check still holds (no regression)."""
    with pytest.raises(ValueError, match="positive"):
        Namespace(name="ns", workload="w", retention_seconds=0)
    with pytest.raises(ValueError, match="positive"):
        Namespace(name="ns", workload="w", retention_seconds=-1.0)


def test_namespace_accepts_finite_positive_retention() -> None:
    ns = Namespace(name="ns", workload="w", retention_seconds=60.0)
    assert ns.retention_seconds == 60.0


def test_namespace_accepts_none_retention() -> None:
    ns = Namespace(name="ns", workload="w", retention_seconds=None)
    assert ns.retention_seconds is None


def test_namespace_resolve_ttl_returns_default_when_call_value_none() -> None:
    """Calling `resolve_ttl(None)` falls through to
    `retention_seconds`. Matches the prior `_ttl` /
    `_effective_ttl` semantics."""
    ns = Namespace(name="ns", workload="w", retention_seconds=30.0)
    assert ns.resolve_ttl(None) == 30.0


def test_namespace_resolve_ttl_returns_call_value_when_set() -> None:
    """An explicit per-call value overrides the namespace default."""
    ns = Namespace(name="ns", workload="w", retention_seconds=30.0)
    assert ns.resolve_ttl(60.0) == 60.0


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_namespace_resolve_ttl_rejects_non_finite_call_value(bad: float) -> None:
    """The per-call `ttl_seconds` gets the same validation as
    `retention_seconds` (BL-197). Centralising the helper closes the
    five-way duplication and makes the validation reachable from
    every adapter's resolver call site."""
    ns = Namespace(name="ns", workload="w")
    with pytest.raises(ValueError, match="finite"):
        ns.resolve_ttl(bad)


def test_namespace_resolve_ttl_rejects_non_positive_call_value() -> None:
    ns = Namespace(name="ns", workload="w")
    with pytest.raises(ValueError, match="positive"):
        ns.resolve_ttl(0)
    with pytest.raises(ValueError, match="positive"):
        ns.resolve_ttl(-1.0)


@pytest.mark.asyncio
async def test_inmemory_write_rejects_non_finite_ttl_via_resolve() -> None:
    """End-to-end: the adapter's `_effective_ttl` now delegates to
    `Namespace.resolve_ttl`, so an in-call NaN reaches the validator
    before becoming `expires_at = NaN`. Regression guard against a
    future adapter regressing to a pass-through helper."""
    store = InMemoryStore(Namespace(name="ns", workload="w"))
    with pytest.raises(ValueError, match="finite"):
        await store.write("k", b"v", ttl_seconds=math.nan)


# --- BL-198: RedisStore.mset empty-batch short-circuit ----------------
#
# RedisStore needs the `redis` extra; the test uses fakeredis. Without
# it the test skips (the in-tree adapters are still covered by the
# unit tests above).


@pytest.mark.asyncio
async def test_redis_mset_empty_is_noop() -> None:
    """An empty `mset({})` short-circuits without round-tripping
    Redis (BL-178 class extension; parity with `mdelete`)."""
    fakeredis = pytest.importorskip("fakeredis")
    from memory.redis import RedisStore

    client = fakeredis.aioredis.FakeRedis()
    store = RedisStore(Namespace(name="ns", workload="w"), client=client)
    # Smoke check: write something, mset({}), the existing value still
    # there. No round-trip for the empty call (we cannot directly
    # observe the pipeline elision from the test, but the `not items`
    # path returns before any pipeline().execute()).
    await store.write("k", b"v")
    await store.mset({})
    assert await store.read("k") == b"v"


# --- BL-199: TTLSweeper failure resilience ----------------------------


class _AlwaysRaisingStore:
    """SweepableStore double whose `sweep_expired` always raises.

    Used to verify that a transient (or persistent) backend failure
    does not kill the TTLSweeper background loop forever (BL-199 /
    BL-189 class).
    """

    namespace = Namespace(name="ns", workload="w")
    name = "always-raising"

    def __init__(self) -> None:
        self.calls = 0

    async def sweep_expired(self) -> int:
        self.calls += 1
        raise RuntimeError("simulated backend blip")


class _IntermittentStore:
    """SweepableStore double that raises once, then succeeds."""

    namespace = Namespace(name="ns", workload="w")
    name = "intermittent"

    def __init__(self) -> None:
        self.calls = 0

    async def sweep_expired(self) -> int:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated transient blip")
        return 3


@pytest.mark.asyncio
async def test_sweeper_survives_persistent_failure() -> None:
    """A persistently failing `sweep_expired` does NOT kill the loop;
    `failures_total` accumulates and `last_error` carries the latest
    exception. Pre-BL-199 the first raise propagated and the task
    died silently."""
    store = _AlwaysRaisingStore()
    assert isinstance(store, SweepableStore)
    # Very short interval so the loop runs multiple times in test time.
    async with TTLSweeper(store, interval_seconds=0.01) as sweeper:
        # Yield repeatedly so the loop runs several iterations.
        for _ in range(20):
            await asyncio.sleep(0.01)
            if store.calls >= 3:
                break
    assert store.calls >= 3, f"sweeper died after {store.calls} calls"
    assert sweeper.failures_total == store.calls
    assert isinstance(sweeper.last_error, RuntimeError)
    assert sweeper.swept_total == 0


@pytest.mark.asyncio
async def test_sweeper_resets_failure_counter_after_recovery() -> None:
    """A transient failure self-heals: after the next successful
    sweep, `failures_total` is back to zero and `last_error` is
    None."""
    store = _IntermittentStore()
    assert isinstance(store, SweepableStore)
    async with TTLSweeper(store, interval_seconds=0.01) as sweeper:
        for _ in range(20):
            await asyncio.sleep(0.01)
            if store.calls >= 2 and sweeper.swept_total >= 3:
                break
    assert sweeper.swept_total >= 3
    assert sweeper.failures_total == 0
    assert sweeper.last_error is None


@pytest.mark.asyncio
async def test_sweeper_cancellation_still_works_after_failure() -> None:
    """`asyncio.CancelledError` from `aclose` is not swallowed; the
    sweeper still tears down cleanly even after the loop has caught
    failures."""
    store = _AlwaysRaisingStore()
    sweeper = TTLSweeper(store, interval_seconds=0.01)
    sweeper.start()
    # Let the loop run at least one cycle to hit the failure handler.
    for _ in range(10):
        await asyncio.sleep(0.01)
        if store.calls >= 1:
            break
    await sweeper.aclose()
    # Calling aclose twice is idempotent (no error).
    await sweeper.aclose()
