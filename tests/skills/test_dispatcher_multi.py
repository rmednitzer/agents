"""Tests for MultiDispatcher (BL-050)."""

from __future__ import annotations

from typing import Any

import pytest

from skills.dispatchers.multi import MultiDispatcher, MultiMode
from skills.types import SkillMatch


class _Fixed:
    """A dispatcher returning a fixed list, for deterministic blending."""

    def __init__(self, name: str, matches: list[tuple[str, float]]) -> None:
        self.name = name
        self._matches = matches

    async def dispatch(
        self, query: str, *, context: dict[str, Any] | None = None, limit: int = 1
    ) -> list[SkillMatch]:
        return [
            SkillMatch(skill_name=s, confidence=c, rationale="x", dispatcher=self.name)
            for s, c in self._matches
        ][:limit]


def test_requires_members_and_aligned_weights() -> None:
    with pytest.raises(ValueError, match="at least one"):
        MultiDispatcher([])
    with pytest.raises(ValueError, match="align"):
        MultiDispatcher([_Fixed("a", [])], weights=[1.0, 2.0])
    with pytest.raises(ValueError, match="WEIGHTED"):
        MultiDispatcher([_Fixed("a", [])], mode=MultiMode.WEIGHTED)


@pytest.mark.asyncio
async def test_vote_mode_rewards_consensus() -> None:
    a = _Fixed("a", [("x", 0.9), ("y", 0.5)])
    b = _Fixed("b", [("x", 0.4)])
    c = _Fixed("c", [("x", 0.3), ("y", 0.8)])
    md = MultiDispatcher([a, b, c], mode=MultiMode.VOTE)
    matches = await md.dispatch("q", limit=2)
    assert matches[0].skill_name == "x"  # 3/3 voters
    assert matches[0].confidence == pytest.approx(1.0)
    assert matches[1].skill_name == "y"  # 2/3 voters
    assert matches[1].confidence == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_average_mode_divides_by_member_count() -> None:
    a = _Fixed("a", [("x", 0.9)])
    b = _Fixed("b", [("x", 0.3)])
    md = MultiDispatcher([a, b], mode=MultiMode.AVERAGE)
    (m,) = await md.dispatch("q", limit=1)
    assert m.confidence == pytest.approx((0.9 + 0.3) / 2)


@pytest.mark.asyncio
async def test_weighted_mode() -> None:
    a = _Fixed("a", [("x", 1.0)])
    b = _Fixed("b", [("x", 0.0)])
    md = MultiDispatcher([a, b], mode=MultiMode.WEIGHTED, weights=[3.0, 1.0])
    (m,) = await md.dispatch("q", limit=1)
    assert m.confidence == pytest.approx(3.0 / 4.0)


@pytest.mark.asyncio
async def test_limit_zero_returns_empty() -> None:
    md = MultiDispatcher([_Fixed("a", [("x", 1.0)])])
    assert await md.dispatch("q", limit=0) == []
