"""BL-245: the structured operational-memory journal layer.

Deterministic: every timestamp is an explicit timezone-aware datetime,
and records are backed by an InMemoryStore so the journal is exercised
without the wall clock or a durable backend.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from memory.inmemory import InMemoryStore
from memory.journal import (
    Decision,
    Event,
    InvalidTransition,
    Journal,
    JournalError,
    Task,
    TaskNotFound,
    TaskStatus,
    Thread,
)
from memory.types import Namespace


def _journal() -> Journal:
    return Journal(InMemoryStore(Namespace(name="journal", workload="w")))


def _dt(minute: int) -> datetime:
    return datetime(2026, 6, 13, 12, minute, tzinfo=UTC)


# --- tasks: the FSM -----------------------------------------------------


async def test_create_task_is_pending_with_creation_log() -> None:
    j = _journal()
    task = await j.create_task("ship the release", now=_dt(0))
    assert isinstance(task, Task)
    assert task.status is TaskStatus.PENDING
    assert task.title == "ship the release"
    assert len(task.log) == 1
    assert task.log[0].from_status is None
    assert task.log[0].to_status is TaskStatus.PENDING
    assert await j.get_task(task.id) == task


async def test_valid_transitions_append_to_log() -> None:
    j = _journal()
    task = await j.create_task("t", now=_dt(0))
    started = await j.transition_task(task.id, TaskStatus.IN_PROGRESS, note="picked up", now=_dt(1))
    assert started.status is TaskStatus.IN_PROGRESS
    done = await j.transition_task(task.id, TaskStatus.DONE, now=_dt(2))
    assert done.status is TaskStatus.DONE
    assert [e.to_status for e in done.log] == [
        TaskStatus.PENDING,
        TaskStatus.IN_PROGRESS,
        TaskStatus.DONE,
    ]
    assert done.log[1].note == "picked up"
    assert done.updated_at == _dt(2)


async def test_illegal_transition_raises() -> None:
    j = _journal()
    task = await j.create_task("t", now=_dt(0))
    # PENDING -> DONE is not in the table.
    with pytest.raises(InvalidTransition, match="cannot move"):
        await j.transition_task(task.id, TaskStatus.DONE)


async def test_terminal_state_has_no_outgoing_transition() -> None:
    j = _journal()
    task = await j.create_task("t", now=_dt(0))
    await j.transition_task(task.id, TaskStatus.CANCELLED, now=_dt(1))
    with pytest.raises(InvalidTransition):
        await j.transition_task(task.id, TaskStatus.IN_PROGRESS, now=_dt(2))


async def test_transition_unknown_task_raises() -> None:
    j = _journal()
    with pytest.raises(TaskNotFound):
        await j.transition_task("nope", TaskStatus.IN_PROGRESS)


async def test_list_tasks_sorted_by_creation() -> None:
    j = _journal()
    a = await j.create_task("a", now=_dt(0))
    b = await j.create_task("b", now=_dt(1))
    assert [t.id for t in await j.list_tasks()] == [a.id, b.id]


async def test_ready_tasks_respects_dependencies() -> None:
    j = _journal()
    dep = await j.create_task("dependency", now=_dt(0))
    blocked = await j.create_task("blocked", depends_on=[dep.id], now=_dt(1))
    free = await j.create_task("free", now=_dt(2))
    # Initially: free is ready, blocked is not (its dep is PENDING).
    ready_ids = {t.id for t in await j.ready_tasks()}
    assert free.id in ready_ids
    assert blocked.id not in ready_ids
    assert dep.id in ready_ids  # no deps of its own
    # Finish the dependency: now the blocked task is ready.
    await j.transition_task(dep.id, TaskStatus.IN_PROGRESS, now=_dt(3))
    await j.transition_task(dep.id, TaskStatus.DONE, now=_dt(4))
    ready_ids = {t.id for t in await j.ready_tasks()}
    assert blocked.id in ready_ids
    assert dep.id not in ready_ids  # no longer PENDING


async def test_ready_tasks_treats_missing_dependency_as_unsatisfied() -> None:
    j = _journal()
    orphan = await j.create_task("orphan", depends_on=["does-not-exist"], now=_dt(0))
    assert orphan.id not in {t.id for t in await j.ready_tasks()}


# --- threads: the stale-after query -------------------------------------


async def test_stale_threads_query() -> None:
    j = _journal()
    thread = await j.open_thread(
        "incident review", next_action_owner="alice", stale_after_seconds=3600, now=_dt(0)
    )
    # Within the window: not stale.
    soon = _dt(0) + timedelta(seconds=1800)
    assert await j.stale_threads(now=soon) == []
    # Past the window: stale.
    later = _dt(0) + timedelta(seconds=7200)
    stale = await j.stale_threads(now=later)
    assert [t.id for t in stale] == [thread.id]


async def test_touch_thread_resets_staleness() -> None:
    j = _journal()
    thread = await j.open_thread(
        "loop", next_action_owner="bob", stale_after_seconds=60, now=_dt(0)
    )
    later = _dt(0) + timedelta(seconds=120)
    assert await j.stale_threads(now=later)  # stale now
    await j.touch_thread(thread.id, now=later)
    assert await j.stale_threads(now=later) == []  # freshened, no longer stale


async def test_touch_unknown_thread_raises() -> None:
    j = _journal()
    with pytest.raises(JournalError, match="not found"):
        await j.touch_thread("nope", now=_dt(0))


async def test_open_thread_rejects_nonpositive_or_nonfinite_window() -> None:
    j = _journal()
    for bad in (0.0, -1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="stale_after_seconds"):
            await j.open_thread("t", next_action_owner="x", stale_after_seconds=bad, now=_dt(0))


# --- decisions: the decision log ----------------------------------------


async def test_decision_log_is_ordered_and_typed() -> None:
    j = _journal()
    first = await j.record_decision(
        "adopt RRF", rationale="tested P@1 gain", related=["task.1"], now=_dt(0)
    )
    second = await j.record_decision("defer LRU", now=_dt(1))
    log = await j.decisions()
    assert [d.id for d in log] == [first.id, second.id]
    assert isinstance(log[0], Decision)
    assert log[0].rationale == "tested P@1 gain"
    assert log[0].related == ("task.1",)


# --- events: the categorized timeline -----------------------------------


async def test_timeline_orders_and_filters_by_category() -> None:
    j = _journal()
    await j.log_event("deploy", "released v1", now=_dt(0))
    await j.log_event("alert", "cpu spike", now=_dt(1))
    await j.log_event("deploy", "released v2", now=_dt(2))
    full = await j.timeline()
    assert [e.summary for e in full] == ["released v1", "cpu spike", "released v2"]
    assert isinstance(full[0], Event)
    deploys = await j.timeline(category="deploy")
    assert [e.summary for e in deploys] == ["released v1", "released v2"]


# --- validation, persistence, isolation ---------------------------------


async def test_empty_required_text_is_rejected() -> None:
    j = _journal()
    with pytest.raises(ValueError, match="title"):
        await j.create_task("", now=_dt(0))
    with pytest.raises(ValueError, match="topic"):
        await j.open_thread("", next_action_owner="x", stale_after_seconds=1, now=_dt(0))
    with pytest.raises(ValueError, match="summary"):
        await j.record_decision("", now=_dt(0))
    with pytest.raises(ValueError, match="category"):
        await j.log_event("", "s", now=_dt(0))


async def test_naive_now_is_rejected() -> None:
    j = _journal()
    naive = datetime(2026, 6, 13, 12, 0)  # deliberately naive
    with pytest.raises(ValueError, match="timezone-aware"):
        await j.create_task("t", now=naive)


async def test_records_persist_through_the_store_and_are_kind_isolated() -> None:
    store = InMemoryStore(Namespace(name="journal", workload="w"))
    j1 = Journal(store)
    task = await j1.create_task("persisted", now=_dt(0))
    await j1.open_thread("t", next_action_owner="x", stale_after_seconds=60, now=_dt(0))
    # A fresh Journal over the same store sees the persisted record.
    j2 = Journal(store)
    assert (await j2.get_task(task.id)) is not None
    # list_tasks does not leak the thread record (kind-prefixed keys).
    assert [t.id for t in await j2.list_tasks()] == [task.id]
    assert len(await j2.list_threads()) == 1


def test_record_models_are_immutable() -> None:
    entry = Thread(
        id="x",
        topic="t",
        next_action_owner="o",
        stale_after_seconds=1.0,
        created_at=_dt(0),
        updated_at=_dt(0),
    )
    with pytest.raises(ValidationError):  # pydantic frozen model
        entry.topic = "changed"  # type: ignore[misc]
