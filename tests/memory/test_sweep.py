"""Active TTL sweep: sweep_expired + TTLSweeper (BL-080)."""

from __future__ import annotations

import asyncio

import pytest

from memory.inmemory import InMemoryStore
from memory.store import SweepableStore
from memory.sweep import TTLSweeper
from memory.types import Namespace


def _store() -> InMemoryStore:
    return InMemoryStore(Namespace(name="sweep", workload="w"))


@pytest.mark.asyncio
async def test_inmemory_is_sweepable() -> None:
    assert isinstance(_store(), SweepableStore)


@pytest.mark.asyncio
async def test_sweep_expired_removes_only_expired_and_counts() -> None:
    s = _store()
    await s.write("keep", b"v")
    await s.write("gone1", b"v", ttl_seconds=0.02)
    await s.write("gone2", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    assert await s.sweep_expired() == 2
    assert await s.sweep_expired() == 0  # idempotent
    assert await s.list_keys() == ["keep"]


@pytest.mark.asyncio
async def test_ttl_sweeper_runs_on_interval() -> None:
    s = _store()
    await s.write("temp", b"v", ttl_seconds=0.02)
    async with TTLSweeper(s, interval_seconds=0.01) as sweeper:
        await asyncio.sleep(0.1)
    assert sweeper.swept_total >= 1


@pytest.mark.asyncio
async def test_ttl_sweeper_start_stop_idempotent() -> None:
    s = _store()
    sweeper = TTLSweeper(s, interval_seconds=0.01)
    sweeper.start()
    sweeper.start()  # idempotent
    await asyncio.sleep(0.03)
    await sweeper.aclose()
    await sweeper.aclose()  # idempotent


@pytest.mark.asyncio
async def test_ttl_sweeper_rejects_nonpositive_interval() -> None:
    with pytest.raises(ValueError, match="positive"):
        TTLSweeper(_store(), interval_seconds=0)
