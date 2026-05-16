"""Tests for LaneDispatcher."""

from __future__ import annotations

from typing import Any

import pytest

from skills.dispatcher import Dispatcher
from skills.dispatchers.lane import LaneDispatcher
from skills.types import SkillMatch


class _StubDispatcher:
    """Returns canned SkillMatches for testing."""

    def __init__(self, name: str, matches: list[SkillMatch]) -> None:
        self.name = name
        self._matches = matches
        self.called_with: list[str] = []

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        self.called_with.append(query)
        return self._matches[:limit]


@pytest.mark.asyncio
async def test_lane_routes_to_correct_sub_dispatcher() -> None:
    router = _StubDispatcher(
        "router",
        [
            SkillMatch(
                skill_name="ops",
                confidence=1.0,
                rationale="ops query",
                dispatcher="router",
            )
        ],
    )
    ops_disp = _StubDispatcher(
        "ops",
        [
            SkillMatch(
                skill_name="deploy",
                confidence=0.9,
                rationale="deploy",
                dispatcher="ops",
            )
        ],
    )
    docs_disp = _StubDispatcher("docs", [])
    lane = LaneDispatcher(router, {"ops": ops_disp, "docs": docs_disp})

    matches = await lane.dispatch("how do I deploy?")
    assert len(matches) == 1
    assert matches[0].skill_name == "deploy"
    assert ops_disp.called_with == ["how do I deploy?"]
    assert docs_disp.called_with == []


@pytest.mark.asyncio
async def test_lane_returns_empty_when_router_finds_nothing() -> None:
    router = _StubDispatcher("router", [])
    sub = _StubDispatcher("sub", [])
    lane = LaneDispatcher(router, {"any": sub})
    matches = await lane.dispatch("x")
    assert matches == []


@pytest.mark.asyncio
async def test_lane_returns_empty_when_lane_not_in_map() -> None:
    router = _StubDispatcher(
        "router",
        [
            SkillMatch(
                skill_name="unknown-lane",
                confidence=1.0,
                rationale="",
                dispatcher="router",
            )
        ],
    )
    sub = _StubDispatcher("sub", [])
    lane = LaneDispatcher(router, {"known": sub})
    matches = await lane.dispatch("x")
    assert matches == []


def test_lane_dispatcher_satisfies_protocol() -> None:
    router = _StubDispatcher("r", [])
    lane = LaneDispatcher(router, {})
    assert isinstance(lane, Dispatcher)
