"""Structured operational memory: the journal layer (BL-245).

The store Protocols (`memory/store.py`) give key/value plus TTL plus
namespace, with extension Protocols for batch / scan / CAS / versioned /
transactional / semantic / bitemporal access. None of them carries a
*cognitive schema*: a task with a status FSM and a dependency edge, an
open thread that goes stale, a decision log, a categorized event
timeline. This module adds that layer as typed records persisted
*through* a `MemoryStore`, so it inherits the store's namespace
isolation, TTL, optional audit, and optional encryption rather than
re-implementing them. It is a driver over the store Protocols (the
`MemoryCompactor` / `TTLSweeper` precedent, ADR 0024 / BL-080), not a new
store Protocol: no adapter changes, nothing to fake (ADR 0004).

The two substantive pieces are the ones the gateway leads with: a task
*FSM with an explicit transition table* (an illegal transition raises
rather than corrupting state, and every transition appends to an
append-only log) and a thread *stale-after* window surfaced by a
staleness query. `Decision` and `Event` are the simpler append-and-list
records (a decision log, a categorized timeline).

Records are immutable pydantic models (copy-on-write via `model_copy`),
serialized to JSON bytes under a `"<kind>.<id>"` key so a single
`list_keys(prefix="<kind>.")` enumerates one record type. The `<id>` is a
uuid4 hex; the create / open / record / log methods return the record so
a caller (and a test) holds the id without parsing keys.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict

from memory.store import MemoryStore

__all__ = [
    "ContextPack",
    "Decision",
    "Event",
    "InvalidTransition",
    "Journal",
    "JournalEntry",
    "JournalError",
    "Task",
    "TaskNotFound",
    "TaskStatus",
    "Thread",
    "context_pack",
]


class JournalError(Exception):
    """Base class for journal-layer errors."""


class TaskNotFound(JournalError):
    """A task id does not resolve to a stored task."""


class InvalidTransition(JournalError):
    """A task status transition is not permitted by the FSM table."""


class TaskStatus(StrEnum):
    """The status states of a journal task.

    ``DONE`` and ``CANCELLED`` are terminal (no outgoing transitions).
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


# The FSM transition table: the set of statuses each status may move to.
# An explicit table (not ad hoc checks) so the legal graph is one
# reviewable object and an illegal transition is a single membership
# test (the gateway's task-FSM pattern).
_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset(
        {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
    ),
    TaskStatus.IN_PROGRESS: frozenset({TaskStatus.BLOCKED, TaskStatus.DONE, TaskStatus.CANCELLED}),
    TaskStatus.BLOCKED: frozenset({TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}),
    TaskStatus.DONE: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


class JournalEntry(BaseModel):
    """One append-only log line on a task's history.

    ``from_status`` is ``None`` for the creation entry; thereafter it is
    the status the task left. Immutable.
    """

    model_config = ConfigDict(frozen=True)

    at: datetime
    from_status: TaskStatus | None
    to_status: TaskStatus
    note: str = ""


class Task(BaseModel):
    """A unit of work with a status FSM, dependencies, and a log.

    Immutable: a transition produces a new ``Task`` (copy-on-write).
    ``depends_on`` are the ids of tasks this one waits on (the dependency
    edge); ``ready`` (via ``Journal.ready_tasks``) means every dependency
    is ``DONE``. ``log`` is the append-only transition history.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    status: TaskStatus = TaskStatus.PENDING
    depends_on: tuple[str, ...] = ()
    log: tuple[JournalEntry, ...] = ()
    created_at: datetime
    updated_at: datetime


class Thread(BaseModel):
    """An open line of work with a next-action owner and a stale-after window.

    A thread is *stale* (via ``Journal.stale_threads``) when the time
    since ``updated_at`` exceeds ``stale_after_seconds``; ``touch_thread``
    marks it fresh again. Immutable.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    topic: str
    next_action_owner: str
    stale_after_seconds: float
    created_at: datetime
    updated_at: datetime


class Decision(BaseModel):
    """A recorded decision with its rationale and related record ids."""

    model_config = ConfigDict(frozen=True)

    id: str
    summary: str
    rationale: str = ""
    related: tuple[str, ...] = ()
    recorded_at: datetime


class Event(BaseModel):
    """A categorized timeline event."""

    model_config = ConfigDict(frozen=True)

    id: str
    category: str
    summary: str
    occurred_at: datetime


_RecordT = TypeVar("_RecordT", bound=BaseModel)


def _require_aware(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"timestamp must be timezone-aware (UTC), got naive {value.isoformat()}")
    return value


def _stamp(now: datetime | None) -> datetime:
    return _require_aware(now) if now is not None else datetime.now(UTC)


def _non_empty(label: str, value: str) -> str:
    if not value:
        raise ValueError(f"{label} must not be empty")
    return value


def _ready_from(tasks: list[Task]) -> list[Task]:
    """PENDING tasks whose every dependency exists and is DONE.

    A missing dependency is treated as unsatisfied (the fail-safe
    reading). Pure over an already-listed task set so `ready_tasks` and
    `context_pack` share one definition over a single listing.
    """
    by_id = {t.id: t for t in tasks}
    ready: list[Task] = []
    for task in tasks:
        if task.status != TaskStatus.PENDING:
            continue
        deps = [by_id.get(dep) for dep in task.depends_on]
        if all(dep is not None and dep.status == TaskStatus.DONE for dep in deps):
            ready.append(task)
    return ready


def _stale_from(threads: list[Thread], instant: datetime) -> list[Thread]:
    """Threads idle past their stale-after window at ``instant``.

    Returned oldest-update-first so the most neglected surface first.
    Pure over an already-listed thread set so `stale_threads` and
    `context_pack` share one definition over a single listing.
    """
    stale = [t for t in threads if (instant - t.updated_at).total_seconds() > t.stale_after_seconds]
    return sorted(stale, key=lambda t: (t.updated_at, t.id))


class Journal:
    """Typed operational-memory records over a `MemoryStore` (BL-245).

    Bound to one store at construction; every record inherits that
    store's namespace, TTL (the namespace default applies on write),
    audit, and encryption. A driver, not a store Protocol, so any
    `MemoryStore` adapter backs it unchanged.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    # --- persistence helpers ------------------------------------------

    @staticmethod
    def _key(kind: str, record_id: str) -> str:
        # "<kind>.<id>" keeps one dot (validate_key forbids ".." but not
        # a single "."), so list_keys(prefix="<kind>.") enumerates a type.
        return f"{kind}.{record_id}"

    async def _put(self, kind: str, record: BaseModel) -> None:
        await self._store.write(
            self._key(kind, record.id),  # type: ignore[attr-defined]
            record.model_dump_json().encode("utf-8"),
        )

    async def _get(self, kind: str, model: type[_RecordT], record_id: str) -> _RecordT | None:
        raw = await self._store.read(self._key(kind, record_id))
        if raw is None:
            return None
        return model.model_validate_json(raw)

    async def _list(self, kind: str, model: type[_RecordT]) -> list[_RecordT]:
        records: list[_RecordT] = []
        for key in await self._store.list_keys(prefix=f"{kind}."):
            raw = await self._store.read(key)
            if raw is not None:
                records.append(model.model_validate_json(raw))
        return records

    # --- tasks (the FSM) ----------------------------------------------

    async def create_task(
        self,
        title: str,
        *,
        depends_on: Iterable[str] = (),
        now: datetime | None = None,
    ) -> Task:
        """Create a PENDING task with a creation log entry."""
        stamp = _stamp(now)
        task = Task(
            id=uuid.uuid4().hex,
            title=_non_empty("title", title),
            status=TaskStatus.PENDING,
            depends_on=tuple(depends_on),
            log=(JournalEntry(at=stamp, from_status=None, to_status=TaskStatus.PENDING),),
            created_at=stamp,
            updated_at=stamp,
        )
        await self._put("task", task)
        return task

    async def get_task(self, task_id: str) -> Task | None:
        return await self._get("task", Task, task_id)

    async def list_tasks(self) -> list[Task]:
        return sorted(await self._list("task", Task), key=lambda t: (t.created_at, t.id))

    async def transition_task(
        self,
        task_id: str,
        to: TaskStatus,
        *,
        note: str = "",
        now: datetime | None = None,
    ) -> Task:
        """Move a task to ``to``, validated against the FSM table.

        Appends a `JournalEntry` and writes the new task back. Raises
        `TaskNotFound` if the id is unknown, or `InvalidTransition` if the
        table does not permit ``status -> to`` (a no-op self-transition is
        rejected too: terminal states have no outgoing edges and the
        table never lists a status as its own successor).
        """
        task = await self.get_task(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        if to not in _TASK_TRANSITIONS[task.status]:
            raise InvalidTransition(
                f"task {task_id} cannot move from {task.status} to {to} "
                f"(allowed: {sorted(_TASK_TRANSITIONS[task.status])})"
            )
        stamp = _stamp(now)
        entry = JournalEntry(at=stamp, from_status=task.status, to_status=to, note=note)
        updated = task.model_copy(
            update={"status": to, "log": (*task.log, entry), "updated_at": stamp}
        )
        await self._put("task", updated)
        return updated

    async def ready_tasks(self) -> list[Task]:
        """PENDING tasks whose every dependency exists and is DONE.

        The dependency-edge query: a task with an absent or unfinished
        dependency is not ready (a missing dependency is treated as
        unsatisfied, the fail-safe reading).
        """
        return _ready_from(await self.list_tasks())

    # --- threads (the stale-after query) ------------------------------

    async def open_thread(
        self,
        topic: str,
        *,
        next_action_owner: str,
        stale_after_seconds: float,
        now: datetime | None = None,
    ) -> Thread:
        """Open a thread with a next-action owner and a stale-after window."""
        if not (stale_after_seconds > 0) or stale_after_seconds == float("inf"):
            raise ValueError(
                f"stale_after_seconds must be finite and positive, got {stale_after_seconds!r}"
            )
        stamp = _stamp(now)
        thread = Thread(
            id=uuid.uuid4().hex,
            topic=_non_empty("topic", topic),
            next_action_owner=_non_empty("next_action_owner", next_action_owner),
            stale_after_seconds=stale_after_seconds,
            created_at=stamp,
            updated_at=stamp,
        )
        await self._put("thread", thread)
        return thread

    async def get_thread(self, thread_id: str) -> Thread | None:
        return await self._get("thread", Thread, thread_id)

    async def list_threads(self) -> list[Thread]:
        return sorted(await self._list("thread", Thread), key=lambda t: (t.created_at, t.id))

    async def touch_thread(self, thread_id: str, *, now: datetime | None = None) -> Thread:
        """Mark a thread active again by advancing ``updated_at``."""
        thread = await self.get_thread(thread_id)
        if thread is None:
            raise JournalError(f"thread {thread_id} not found")
        updated = thread.model_copy(update={"updated_at": _stamp(now)})
        await self._put("thread", updated)
        return updated

    async def stale_threads(self, *, now: datetime | None = None) -> list[Thread]:
        """Threads whose idle time exceeds their stale-after window.

        A thread is stale when ``now - updated_at > stale_after_seconds``.
        Returned oldest-update-first so the most neglected surface first.
        """
        return _stale_from(await self.list_threads(), _stamp(now))

    # --- decisions (the decision log) ---------------------------------

    async def record_decision(
        self,
        summary: str,
        *,
        rationale: str = "",
        related: Iterable[str] = (),
        now: datetime | None = None,
    ) -> Decision:
        """Append a decision to the log."""
        decision = Decision(
            id=uuid.uuid4().hex,
            summary=_non_empty("summary", summary),
            rationale=rationale,
            related=tuple(related),
            recorded_at=_stamp(now),
        )
        await self._put("decision", decision)
        return decision

    async def decisions(self) -> list[Decision]:
        """Every decision, oldest first."""
        return sorted(await self._list("decision", Decision), key=lambda d: (d.recorded_at, d.id))

    # --- events (the categorized timeline) ----------------------------

    async def log_event(
        self,
        category: str,
        summary: str,
        *,
        now: datetime | None = None,
    ) -> Event:
        """Append an event to the timeline under ``category``."""
        event = Event(
            id=uuid.uuid4().hex,
            category=_non_empty("category", category),
            summary=_non_empty("summary", summary),
            occurred_at=_stamp(now),
        )
        await self._put("event", event)
        return event

    async def timeline(self, *, category: str | None = None) -> list[Event]:
        """Events oldest first, optionally filtered to one ``category``."""
        events = await self._list("event", Event)
        if category is not None:
            events = [e for e in events if e.category == category]
        return sorted(events, key=lambda e: (e.occurred_at, e.id))


class ContextPack(BaseModel):
    """A session-start snapshot assembled from a `Journal` (BL-249).

    The operational context a fresh session needs to pick up where the
    last one left off: what is actionable now (`ready_tasks`,
    `in_progress_tasks`), what is neglected (`stale_threads`) versus still
    fresh (`open_threads`), and the latest reasoning (`recent_decisions`).
    Immutable; `context_pack` builds it.
    """

    model_config = ConfigDict(frozen=True)

    ready_tasks: tuple[Task, ...]
    in_progress_tasks: tuple[Task, ...]
    stale_threads: tuple[Thread, ...]
    open_threads: tuple[Thread, ...]
    recent_decisions: tuple[Decision, ...]


async def context_pack(
    journal: Journal,
    *,
    now: datetime | None = None,
    recent_decisions: int = 5,
) -> ContextPack:
    """Assemble a session-rehydration `ContextPack` from ``journal`` (BL-249).

    The session-start context refresh: the ready and in-progress tasks,
    the stale threads (idle past their window at ``now``) split from the
    still-fresh open threads, and the most recent ``recent_decisions``
    decisions. Read-only; the hardened single-shot / scheduled envelope a
    workload runs this inside is a deployment pattern (ADR 0037), not a
    contract change. ``recent_decisions`` must be non-negative.

    Tasks and threads are each listed once and the ready / in-progress
    and stale / open splits derived in memory, since the listing is
    per-key and a second pass would double the reads.
    """
    if recent_decisions < 0:
        raise ValueError(f"recent_decisions must be non-negative, got {recent_decisions}")
    tasks = await journal.list_tasks()
    ready = _ready_from(tasks)
    in_progress = [t for t in tasks if t.status == TaskStatus.IN_PROGRESS]
    instant = _stamp(now)
    threads = await journal.list_threads()
    stale = _stale_from(threads, instant)
    stale_ids = {t.id for t in stale}
    open_threads = [t for t in threads if t.id not in stale_ids]
    decisions = await journal.decisions()
    tail = decisions[-recent_decisions:] if recent_decisions else []
    return ContextPack(
        ready_tasks=tuple(ready),
        in_progress_tasks=tuple(in_progress),
        stale_threads=tuple(stale),
        open_threads=tuple(open_threads),
        recent_decisions=tuple(tail),
    )
