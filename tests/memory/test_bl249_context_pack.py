"""BL-249: session rehydration via context_pack over the journal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from memory.inmemory import InMemoryStore
from memory.journal import ContextPack, Journal, TaskStatus, context_pack
from memory.types import Namespace


def _journal() -> Journal:
    return Journal(InMemoryStore(Namespace(name="journal", workload="w")))


def _dt(minute: int) -> datetime:
    return datetime(2026, 6, 13, 12, minute, tzinfo=UTC)


async def test_empty_journal_packs_empty() -> None:
    pack = await context_pack(_journal(), now=_dt(0))
    assert isinstance(pack, ContextPack)
    assert pack.ready_tasks == ()
    assert pack.in_progress_tasks == ()
    assert pack.stale_threads == ()
    assert pack.open_threads == ()
    assert pack.recent_decisions == ()


async def test_context_pack_assembles_actionable_state() -> None:
    j = _journal()
    ready = await j.create_task("ready", now=_dt(0))
    active = await j.create_task("active", now=_dt(1))
    await j.transition_task(active.id, TaskStatus.IN_PROGRESS, now=_dt(2))
    pack = await context_pack(j, now=_dt(3))
    assert [t.id for t in pack.ready_tasks] == [ready.id]
    assert [t.id for t in pack.in_progress_tasks] == [active.id]


async def test_context_pack_splits_stale_from_open_threads() -> None:
    j = _journal()
    fresh = await j.open_thread(
        "fresh", next_action_owner="a", stale_after_seconds=3600, now=_dt(0)
    )
    stale = await j.open_thread("stale", next_action_owner="b", stale_after_seconds=60, now=_dt(0))
    pack = await context_pack(j, now=_dt(0) + timedelta(seconds=120))
    assert [t.id for t in pack.stale_threads] == [stale.id]
    assert [t.id for t in pack.open_threads] == [fresh.id]


async def test_context_pack_recent_decisions_tail() -> None:
    j = _journal()
    ids = []
    for i in range(7):
        d = await j.record_decision(f"decision {i}", now=_dt(i))
        ids.append(d.id)
    pack = await context_pack(j, now=_dt(8), recent_decisions=3)
    assert [d.id for d in pack.recent_decisions] == ids[-3:]  # the 3 most recent


async def test_context_pack_zero_recent_decisions() -> None:
    j = _journal()
    await j.record_decision("d", now=_dt(0))
    pack = await context_pack(j, now=_dt(1), recent_decisions=0)
    assert pack.recent_decisions == ()


async def test_context_pack_rejects_negative_recent_decisions() -> None:
    with pytest.raises(ValueError, match="recent_decisions"):
        await context_pack(_journal(), now=_dt(0), recent_decisions=-1)


def test_context_pack_is_frozen() -> None:
    pack = ContextPack(
        ready_tasks=(),
        in_progress_tasks=(),
        stale_threads=(),
        open_threads=(),
        recent_decisions=(),
    )
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        pack.ready_tasks = ()  # type: ignore[misc]
