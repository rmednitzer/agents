"""BL-247: decay_strength reference + rank_key demotion hook.

Covers:

- decay_strength arithmetic (half-life decay, access reinforcement,
  importance scaling, recency ordering) and its finite / non-negative
  input validation (the BL-159 / BL-231 non-finite-control class);
- TieredMemoryStore.demote_to_capacity(rank_key=...) overriding the
  default first-write FIFO order, with decay_strength as the ranker and
  lexicographic tie-breaks, the default (rank_key=None) unchanged.
"""

from __future__ import annotations

import math

import pytest

from memory.inmemory import InMemoryStore
from memory.tiering import TieredMemoryStore, decay_strength
from memory.types import Namespace


def _ns(retention_seconds: float | None = None) -> Namespace:
    return Namespace(name="tier", workload="w", retention_seconds=retention_seconds)


def _tiered() -> tuple[TieredMemoryStore, InMemoryStore, InMemoryStore]:
    hot = InMemoryStore(_ns())
    cold = InMemoryStore(_ns())
    return TieredMemoryStore(hot, cold), hot, cold


# --- decay_strength arithmetic ------------------------------------------


def test_zero_age_no_access_is_importance() -> None:
    assert decay_strength(1.0, 0.0) == pytest.approx(1.0)
    assert decay_strength(0.5, 0.0) == pytest.approx(0.5)
    assert decay_strength(0.0, 0.0) == pytest.approx(0.0)


def test_halves_at_each_half_life() -> None:
    assert decay_strength(1.0, 100.0, half_life_seconds=100.0) == pytest.approx(0.5)
    assert decay_strength(1.0, 200.0, half_life_seconds=100.0) == pytest.approx(0.25)


def test_access_count_reinforces() -> None:
    base = decay_strength(1.0, 0.0, 0)
    assert decay_strength(1.0, 0.0, 1) == pytest.approx(base * 1.2)
    assert decay_strength(1.0, 0.0, 5) == pytest.approx(base * 2.0)


def test_older_is_weaker_for_same_importance() -> None:
    recent = decay_strength(1.0, 10.0, half_life_seconds=100.0)
    old = decay_strength(1.0, 1000.0, half_life_seconds=100.0)
    assert old < recent


@pytest.mark.parametrize(
    "kwargs",
    [
        {"importance": math.nan, "age_seconds": 0.0},
        {"importance": math.inf, "age_seconds": 0.0},
        {"importance": 1.0, "age_seconds": math.nan},
        {"importance": 1.0, "age_seconds": math.inf},
        {"importance": -1.0, "age_seconds": 0.0},
        {"importance": 1.0, "age_seconds": -1.0},
    ],
)
def test_rejects_non_finite_or_negative_inputs(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="must be"):
        decay_strength(**kwargs)


def test_rejects_bad_config() -> None:
    with pytest.raises(ValueError, match="must be"):
        decay_strength(1.0, 0.0, half_life_seconds=0.0)
    with pytest.raises(ValueError, match="must be"):
        decay_strength(1.0, 0.0, half_life_seconds=math.nan)
    with pytest.raises(ValueError, match="must be"):
        decay_strength(1.0, 0.0, reinforcement=-0.1)
    with pytest.raises(ValueError, match="must be"):
        decay_strength(1.0, 0.0, access_count=-1)


# --- rank_key demotion hook ---------------------------------------------


@pytest.mark.asyncio
async def test_default_demotion_is_first_write_fifo() -> None:
    store, hot, cold = _tiered()
    for key in ("a", "b", "c"):
        await store.write(key, key.encode())
    moved = await store.demote_to_capacity(1)
    assert moved == 2
    assert await hot.list_keys() == ["c"]  # newest write stays hot
    assert sorted(await cold.list_keys()) == ["a", "b"]


@pytest.mark.asyncio
async def test_rank_key_overrides_fifo() -> None:
    store, hot, cold = _tiered()
    for key in ("a", "b", "c"):
        await store.write(key, key.encode())
    # a strongest, c weakest: the two weakest leave hot despite a being
    # the oldest write (FIFO would have kept c).
    strength = {"a": 3.0, "b": 2.0, "c": 1.0}
    moved = await store.demote_to_capacity(1, rank_key=lambda key: strength[key])
    assert moved == 2
    assert await hot.list_keys() == ["a"]
    assert sorted(await cold.list_keys()) == ["b", "c"]


@pytest.mark.asyncio
async def test_rank_key_with_decay_strength_keeps_recent() -> None:
    store, hot, cold = _tiered()
    for key in ("old", "new"):
        await store.write(key, key.encode())
    ages = {"old": 10_000.0, "new": 1.0}
    moved = await store.demote_to_capacity(
        1, rank_key=lambda key: decay_strength(1.0, ages[key], half_life_seconds=100.0)
    )
    assert moved == 1
    assert await hot.list_keys() == ["new"]
    assert await cold.list_keys() == ["old"]


@pytest.mark.asyncio
async def test_rank_key_ties_broken_lexicographically() -> None:
    store, hot, _ = _tiered()
    for key in ("z", "y", "x"):
        await store.write(key, b"v")
    moved = await store.demote_to_capacity(1, rank_key=lambda key: 1.0)
    assert moved == 2
    assert await hot.list_keys() == ["z"]  # x, y demoted on the tie
