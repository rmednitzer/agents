"""BL-250: BitemporalMemoryStore (validity time vs transaction time).

Deterministic: every timestamp is an explicit timezone-aware datetime,
so the two time axes (validity, transaction) are exercised without the
wall clock.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from memory.bitemporal import (
    BitemporalFact,
    BitemporalMemoryStore,
    InMemoryBitemporalStore,
)
from memory.errors import NamespaceViolation


def _dt(month: int, day: int = 1) -> datetime:
    return datetime(2026, month, day, tzinfo=UTC)


async def test_record_returns_open_unsuperseded_fact() -> None:
    store = InMemoryBitemporalStore()
    fact = await store.record("axiom", "role", b"compute", valid_from=_dt(1), recorded_at=_dt(2))
    assert isinstance(fact, BitemporalFact)
    assert fact.subject == "axiom"
    assert fact.predicate == "role"
    assert fact.value == b"compute"
    assert fact.confidence == 1.0
    assert fact.valid_to is None
    assert fact.superseded_at is None
    assert fact.superseded_by is None
    assert len(fact.fact_id) == 64  # sha256 hex


async def test_record_auto_supersedes_prior_current() -> None:
    store = InMemoryBitemporalStore()
    first = await store.record("axiom", "role", b"compute", valid_from=_dt(1), recorded_at=_dt(2))
    second = await store.record(
        "axiom", "role", b"workstation", valid_from=_dt(1), recorded_at=_dt(3)
    )
    history = await store.history("axiom", "role")
    assert [f.value for f in history] == [b"compute", b"workstation"]
    # The prior belief is now superseded, pointing at its successor.
    assert history[0].superseded_at == _dt(3)
    assert history[0].superseded_by == second.fact_id
    # The new belief is open.
    assert history[1].superseded_at is None
    assert second.fact_id != first.fact_id


async def test_current_returns_live_belief_valid_now() -> None:
    store = InMemoryBitemporalStore()
    await store.record("axiom", "role", b"compute", valid_from=_dt(1), recorded_at=_dt(2))
    current = await store.current("axiom", "role", now=_dt(6))
    assert current is not None
    assert current.value == b"compute"


async def test_current_none_when_validity_window_excludes_now() -> None:
    store = InMemoryBitemporalStore()
    # Valid only in [March, June): a query in February sees no live belief.
    await store.record(
        "axiom", "role", b"compute", valid_from=_dt(3), valid_to=_dt(6), recorded_at=_dt(2)
    )
    assert await store.current("axiom", "role", now=_dt(2, 15)) is None
    assert (await store.current("axiom", "role", now=_dt(4))).value == b"compute"  # type: ignore[union-attr]
    # valid_to is exclusive.
    assert await store.current("axiom", "role", now=_dt(6)) is None


async def test_current_follows_supersession() -> None:
    store = InMemoryBitemporalStore()
    await store.record("axiom", "role", b"compute", valid_from=_dt(1), recorded_at=_dt(2))
    await store.record("axiom", "role", b"workstation", valid_from=_dt(1), recorded_at=_dt(3))
    current = await store.current("axiom", "role", now=_dt(6))
    assert current is not None
    assert current.value == b"workstation"  # the live belief, not the superseded one


async def test_current_none_for_unknown_pair() -> None:
    store = InMemoryBitemporalStore()
    assert await store.current("nobody", "nothing", now=_dt(1)) is None


async def test_as_of_distinguishes_transaction_time() -> None:
    # The bitemporal headline: what we believed THEN about a validity time.
    store = InMemoryBitemporalStore()
    await store.record("axiom", "role", b"compute", valid_from=_dt(1), recorded_at=_dt(2))
    await store.record("axiom", "role", b"workstation", valid_from=_dt(1), recorded_at=_dt(4))
    # Believed in March (before the April revision): still "compute".
    before = await store.as_of("axiom", "role", valid_at=_dt(6), known_at=_dt(3))
    assert before is not None
    assert before.value == b"compute"
    # Believed in May (after the revision): "workstation".
    after = await store.as_of("axiom", "role", valid_at=_dt(6), known_at=_dt(5))
    assert after is not None
    assert after.value == b"workstation"


async def test_as_of_distinguishes_validity_time() -> None:
    store = InMemoryBitemporalStore()
    # One belief, recorded once, but with a bounded validity window.
    await store.record(
        "axiom", "status", b"up", valid_from=_dt(2), valid_to=_dt(5), recorded_at=_dt(1)
    )
    known = _dt(6)  # well after it was recorded
    assert (await store.as_of("axiom", "status", valid_at=_dt(3), known_at=known)).value == b"up"  # type: ignore[union-attr]
    # Outside the validity window, nothing was true, even though it was believed.
    assert await store.as_of("axiom", "status", valid_at=_dt(1), known_at=known) is None
    assert await store.as_of("axiom", "status", valid_at=_dt(5), known_at=known) is None


async def test_as_of_none_before_anything_recorded() -> None:
    store = InMemoryBitemporalStore()
    await store.record("axiom", "role", b"compute", valid_from=_dt(1), recorded_at=_dt(4))
    # Queried as known at a time before the fact was recorded.
    assert await store.as_of("axiom", "role", valid_at=_dt(6), known_at=_dt(2)) is None


async def test_history_is_empty_for_unknown_pair() -> None:
    store = InMemoryBitemporalStore()
    assert await store.history("nobody", "nothing") == []


async def test_confidence_is_stored_and_validated() -> None:
    store = InMemoryBitemporalStore()
    fact = await store.record(
        "axiom", "role", b"compute", valid_from=_dt(1), recorded_at=_dt(2), confidence=0.6
    )
    assert fact.confidence == 0.6
    for bad in (-0.1, 1.1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="confidence"):
            await store.record(
                "axiom", "role", b"x", valid_from=_dt(1), recorded_at=_dt(2), confidence=bad
            )


async def test_naive_datetime_is_rejected() -> None:
    store = InMemoryBitemporalStore()
    naive = datetime(2026, 1, 1)  # deliberately naive for the guard test
    with pytest.raises(ValueError, match="timezone-aware"):
        await store.record("axiom", "role", b"x", valid_from=naive, recorded_at=_dt(2))
    with pytest.raises(ValueError, match="timezone-aware"):
        await store.record("axiom", "role", b"x", valid_from=_dt(1), recorded_at=naive)


async def test_inverted_validity_interval_is_rejected() -> None:
    store = InMemoryBitemporalStore()
    with pytest.raises(ValueError, match="valid_to"):
        await store.record(
            "axiom", "role", b"x", valid_from=_dt(6), valid_to=_dt(3), recorded_at=_dt(1)
        )


async def test_invalid_subject_or_predicate_is_rejected() -> None:
    store = InMemoryBitemporalStore()
    with pytest.raises(NamespaceViolation):
        await store.record("has space", "role", b"x", valid_from=_dt(1), recorded_at=_dt(2))
    with pytest.raises(NamespaceViolation):
        await store.record("axiom", "bad/predicate", b"x", valid_from=_dt(1), recorded_at=_dt(2))


def test_reference_satisfies_protocol() -> None:
    assert isinstance(InMemoryBitemporalStore(), BitemporalMemoryStore)


async def test_distinct_pairs_are_isolated() -> None:
    store = InMemoryBitemporalStore()
    await store.record("axiom", "role", b"compute", valid_from=_dt(1), recorded_at=_dt(2))
    await store.record("atlas", "role", b"workstation", valid_from=_dt(1), recorded_at=_dt(2))
    assert (await store.current("axiom", "role", now=_dt(6))).value == b"compute"  # type: ignore[union-attr]
    assert (await store.current("atlas", "role", now=_dt(6))).value == b"workstation"  # type: ignore[union-attr]
