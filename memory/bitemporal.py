"""Bitemporal fact store: validity time vs transaction time (BL-250).

A key/value `MemoryStore` records *what* a value is, not *when it was
true* versus *when the system learned it*. An agent reasoning about a
changing world needs both axes: a fact ("axiom's role is compute") has a
*validity* interval (when it holds in the world) that is independent of
its *transaction* interval (when this store believed it). Revising a
belief should not erase the prior one, so an agent can ask both "what is
true now?" and "what did we believe last week about last month?".

This module ships the `BitemporalMemoryStore` Protocol (the BL-247
held-out half, ADR 0028) and `InMemoryBitemporalStore`, the in-process
reference (the BL-072 / BL-124 "Protocol plus reference first" cadence).

Why a standalone Protocol, not a `MemoryStore` extension: the L2
extensions (Batch / Scan / ContentAddressable / CAS / Versioned /
Semantic, `memory/store.py`) all add operations to the *same*
key-addressed model, so they extend `MemoryStore`. A bitemporal fact is
addressed by `(subject, predicate)` plus two time axes, a different
data model, so this is a sibling Protocol in the memory package rather
than a `MemoryStore` extension (the same stance ADR 0024 took for the
`MemoryCompactor` driver and the `TieredMemoryStore` composition). The
optional `MemoryRead` / `MemoryWrite` audit surface is keyed by a
single key and stays with the k/v adapters; a fact-keyed audit travels
with the journal layer (BL-245). Durable adapters are a follow-up.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from memory.validators import validate_key

__all__ = [
    "BitemporalFact",
    "BitemporalMemoryStore",
    "InMemoryBitemporalStore",
]


@dataclass(frozen=True)
class BitemporalFact:
    """One recorded belief about a `(subject, predicate)`, bitemporal.

    Validity time (`valid_from` / `valid_to`) is when the fact holds in
    the world; transaction time (`recorded_at` / `superseded_at`) is when
    this store held it as a belief. `valid_to` / `superseded_at` of
    `None` mean open-ended (still valid / still believed). `superseded_by`
    is the `fact_id` of the record that replaced this belief, set when a
    newer value is recorded for the same `(subject, predicate)`.
    `confidence` is in [0, 1]. All datetimes are timezone-aware (UTC by
    convention); the store rejects naive datetimes so the two axes never
    raise on a naive-vs-aware comparison.
    """

    fact_id: str
    subject: str
    predicate: str
    value: bytes
    confidence: float
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    superseded_at: datetime | None
    superseded_by: str | None


@runtime_checkable
class BitemporalMemoryStore(Protocol):
    """A bitemporal fact store, addressed by `(subject, predicate)`.

    `record` appends a new belief and auto-supersedes the prior current
    belief for the same `(subject, predicate)`. `current` is the live
    point query (believed-now, valid-now); `as_of` is the full bitemporal
    point query (believed-at `known_at`, valid-at `valid_at`); `history`
    is the full append-only record sequence. All datetimes are
    timezone-aware. A backend implements this only if it can index the
    two time axes, rather than faking it (ADR 0004).
    """

    async def record(
        self,
        subject: str,
        predicate: str,
        value: bytes,
        *,
        valid_from: datetime,
        valid_to: datetime | None = None,
        confidence: float = 1.0,
        recorded_at: datetime | None = None,
    ) -> BitemporalFact: ...

    async def current(
        self,
        subject: str,
        predicate: str,
        *,
        now: datetime | None = None,
    ) -> BitemporalFact | None: ...

    async def as_of(
        self,
        subject: str,
        predicate: str,
        *,
        valid_at: datetime,
        known_at: datetime | None = None,
    ) -> BitemporalFact | None: ...

    async def history(self, subject: str, predicate: str) -> list[BitemporalFact]: ...


def _require_aware(label: str, value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware (UTC), got naive {value.isoformat()}")


def _valid_at(fact: BitemporalFact, instant: datetime) -> bool:
    """Whether ``fact``'s validity interval [valid_from, valid_to) covers ``instant``."""
    if instant < fact.valid_from:
        return False
    return fact.valid_to is None or instant < fact.valid_to


def _believed_at(fact: BitemporalFact, instant: datetime) -> bool:
    """Whether ``fact`` was the store's belief at transaction-time ``instant``.

    True iff it had been recorded by then and had not yet been superseded.
    """
    if instant < fact.recorded_at:
        return False
    return fact.superseded_at is None or instant < fact.superseded_at


class InMemoryBitemporalStore:
    """In-process `BitemporalMemoryStore` reference.

    Facts are held per `(subject, predicate)` in record order, serialized
    by an `asyncio.Lock` (last-write-wins within one instance, like
    `InMemoryStore`). `subject` and `predicate` follow the memory key
    rules (`validate_key`): a workload needing freer text normalizes
    first. Not for production multi-process use; the deterministic backend
    for tests and the reference for adapter authors.
    """

    name: str = "in-memory-bitemporal"

    def __init__(self) -> None:
        self._facts: dict[tuple[str, str], list[BitemporalFact]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _validate(subject: str, predicate: str) -> None:
        validate_key(subject)
        validate_key(predicate)

    @staticmethod
    def _fact_id(subject: str, predicate: str, seq: int) -> str:
        # Deterministic and collision-free per store: the record index
        # disambiguates two facts with identical content and timestamp.
        raw = f"{subject}\0{predicate}\0{seq}".encode()
        return hashlib.sha256(raw).hexdigest()

    async def record(
        self,
        subject: str,
        predicate: str,
        value: bytes,
        *,
        valid_from: datetime,
        valid_to: datetime | None = None,
        confidence: float = 1.0,
        recorded_at: datetime | None = None,
    ) -> BitemporalFact:
        """Append a new belief, superseding the prior current one.

        The previous non-superseded fact for `(subject, predicate)` (if
        any) gets `superseded_at = recorded_at` and `superseded_by` set to
        the new fact's id, atomically with the append. Raises `ValueError`
        on a naive datetime, a non-finite or out-of-range `confidence`, or
        a non-positive validity interval (`valid_to <= valid_from`).
        """
        self._validate(subject, predicate)
        stamp = recorded_at if recorded_at is not None else datetime.now(UTC)
        _require_aware("recorded_at", stamp)
        _require_aware("valid_from", valid_from)
        if valid_to is not None:
            _require_aware("valid_to", valid_to)
            if valid_to <= valid_from:
                raise ValueError(
                    f"valid_to {valid_to.isoformat()} must be after "
                    f"valid_from {valid_from.isoformat()}"
                )
        if not math.isfinite(confidence) or not (0.0 <= confidence <= 1.0):
            raise ValueError(f"confidence must be finite in [0, 1], got {confidence!r}")

        async with self._lock:
            records = self._facts.setdefault((subject, predicate), [])
            fact = BitemporalFact(
                fact_id=self._fact_id(subject, predicate, len(records)),
                subject=subject,
                predicate=predicate,
                value=value,
                confidence=confidence,
                valid_from=valid_from,
                valid_to=valid_to,
                recorded_at=stamp,
                superseded_at=None,
                superseded_by=None,
            )
            for i, prior in enumerate(records):
                if prior.superseded_at is None:
                    records[i] = replace(prior, superseded_at=stamp, superseded_by=fact.fact_id)
            records.append(fact)
            return fact

    async def current(
        self,
        subject: str,
        predicate: str,
        *,
        now: datetime | None = None,
    ) -> BitemporalFact | None:
        """The live belief: the non-superseded fact valid at `now`.

        `now` defaults to the wall clock (UTC). Returns `None` when no
        current belief covers `now` (none recorded, or the live belief's
        validity window does not contain `now`).
        """
        instant = now if now is not None else datetime.now(UTC)
        _require_aware("now", instant)
        self._validate(subject, predicate)
        async with self._lock:
            for fact in self._facts.get((subject, predicate), []):
                if fact.superseded_at is None and _valid_at(fact, instant):
                    return fact
        return None

    async def as_of(
        self,
        subject: str,
        predicate: str,
        *,
        valid_at: datetime,
        known_at: datetime | None = None,
    ) -> BitemporalFact | None:
        """The bitemporal point query: belief at `known_at` about `valid_at`.

        Returns the fact that was the store's belief at transaction-time
        `known_at` (default now) whose validity interval covers `valid_at`,
        or `None` if there was no such belief. Among overlapping records
        the most recently recorded believed-then fact wins (a later
        in-window revision supersedes an earlier one).
        """
        known = known_at if known_at is not None else datetime.now(UTC)
        _require_aware("known_at", known)
        _require_aware("valid_at", valid_at)
        self._validate(subject, predicate)
        async with self._lock:
            match: BitemporalFact | None = None
            for fact in self._facts.get((subject, predicate), []):
                if _believed_at(fact, known) and _valid_at(fact, valid_at):
                    match = fact
            return match

    async def history(self, subject: str, predicate: str) -> list[BitemporalFact]:
        """Every recorded fact for `(subject, predicate)`, in record order."""
        self._validate(subject, predicate)
        async with self._lock:
            return list(self._facts.get((subject, predicate), []))
