"""LaneDispatcher: hierarchical routing through lanes."""

from __future__ import annotations

from typing import Any

from skills.dispatcher import Dispatcher
from skills.types import SkillMatch

__all__ = ["LaneDispatcher"]


class LaneDispatcher:
    """Hierarchical dispatcher.

    Step 1: a router dispatcher selects a lane name for the query. The
    router's returned skill_name is interpreted as the lane name.

    Step 2: a per-lane dispatcher dispatches within the selected lane.

    Maps to the assurance-dispatcher pattern: a top-level routing
    decision selects one of N lanes, then a lane-specific dispatcher
    handles the within-lane selection.
    """

    name: str = "lane"

    def __init__(
        self,
        router: Dispatcher,
        per_lane: dict[str, Dispatcher],
    ) -> None:
        self._router = router
        self._per_lane = per_lane

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        lane_matches = await self._router.dispatch(query, context=context, limit=1)
        if not lane_matches:
            return []
        lane_name = lane_matches[0].skill_name
        lane_dispatcher = self._per_lane.get(lane_name)
        if lane_dispatcher is None:
            return []
        return await lane_dispatcher.dispatch(query, context=context, limit=limit)
