"""Tests for RoutingChainDispatcher."""

from __future__ import annotations

from typing import Any

import pytest

from skills.dispatchers.chain import RoutingChainDispatcher
from skills.types import SkillMatch


class _StubDispatcher:
    def __init__(self, name: str, matches: list[SkillMatch]) -> None:
        self.name = name
        self._matches = matches
        self.call_count = 0

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        self.call_count += 1
        return self._matches[:limit]


def _match(name: str, confidence: float, dispatcher: str = "stub") -> SkillMatch:
    return SkillMatch(
        skill_name=name,
        confidence=confidence,
        rationale="",
        dispatcher=dispatcher,
    )


@pytest.mark.asyncio
async def test_first_above_threshold_wins() -> None:
    cheap = _StubDispatcher("cheap", [_match("x", 0.9)])
    expensive = _StubDispatcher("expensive", [_match("y", 1.0)])
    chain = RoutingChainDispatcher([cheap, expensive], threshold=0.6)
    matches = await chain.dispatch("q")
    assert matches[0].skill_name == "x"
    assert cheap.call_count == 1
    assert expensive.call_count == 0


@pytest.mark.asyncio
async def test_falls_through_below_threshold() -> None:
    cheap = _StubDispatcher("cheap", [_match("x", 0.3)])
    expensive = _StubDispatcher("expensive", [_match("y", 0.9)])
    chain = RoutingChainDispatcher([cheap, expensive], threshold=0.6)
    matches = await chain.dispatch("q")
    assert matches[0].skill_name == "y"
    assert cheap.call_count == 1
    assert expensive.call_count == 1


@pytest.mark.asyncio
async def test_falls_through_empty_match() -> None:
    cheap = _StubDispatcher("cheap", [])
    expensive = _StubDispatcher("expensive", [_match("y", 0.9)])
    chain = RoutingChainDispatcher([cheap, expensive], threshold=0.6)
    matches = await chain.dispatch("q")
    assert matches[0].skill_name == "y"


@pytest.mark.asyncio
async def test_returns_last_matches_when_none_meet_threshold() -> None:
    a = _StubDispatcher("a", [_match("x", 0.4)])
    b = _StubDispatcher("b", [_match("y", 0.5)])
    chain = RoutingChainDispatcher([a, b], threshold=0.9)
    matches = await chain.dispatch("q")
    # Best-effort fallback: returns last non-empty result
    assert matches[0].skill_name == "y"


@pytest.mark.asyncio
async def test_empty_chain_returns_empty() -> None:
    chain = RoutingChainDispatcher([], threshold=0.6)
    matches = await chain.dispatch("q")
    assert matches == []


@pytest.mark.asyncio
async def test_all_empty_returns_empty() -> None:
    a = _StubDispatcher("a", [])
    b = _StubDispatcher("b", [])
    chain = RoutingChainDispatcher([a, b], threshold=0.6)
    matches = await chain.dispatch("q")
    assert matches == []
