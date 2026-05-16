"""Dispatcher Protocol.

A dispatcher routes a natural-language query to one or more skills in a
SkillRegistry. Implementations live in skills.dispatchers. All
dispatchers must be pure: no side effects, no mutation of registry or
context. Logging happens at the harness layer via SkillDispatched events.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from skills.types import SkillMatch

__all__ = ["Dispatcher"]


@runtime_checkable
class Dispatcher(Protocol):
    """Routes a query to one or more skills.

    Implementations expose:
    - name: stable string identifier (used in SkillMatch.dispatcher and
      in event emission).
    - dispatch: async method returning up to `limit` SkillMatches,
      ordered by descending confidence.

    Dispatchers do not invoke skills. They return matches; the calling
    workload or orchestrator decides what to do with them.
    """

    name: str

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]: ...
