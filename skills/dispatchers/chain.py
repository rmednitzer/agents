"""RoutingChainDispatcher: cheap-first fallback chain."""

from __future__ import annotations

from typing import Any

from skills.dispatcher import Dispatcher
from skills.types import SkillMatch

__all__ = ["RoutingChainDispatcher"]


class RoutingChainDispatcher:
    """Tries dispatchers in order until one returns a match above threshold.

    Use cheap dispatchers first (KeywordDispatcher), expensive
    dispatchers last (LLMDispatcher). The first dispatcher whose top
    match has confidence >= threshold wins.

    If no dispatcher in the chain meets the threshold, returns the
    matches from the last dispatcher in the chain that returned
    anything (best-effort fallback). If no dispatcher returns any
    match, returns an empty list.
    """

    name: str = "chain"

    def __init__(
        self,
        chain: list[Dispatcher],
        *,
        threshold: float = 0.6,
    ) -> None:
        self._chain = chain
        self._threshold = threshold

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        last_matches: list[SkillMatch] = []
        for dispatcher in self._chain:
            matches = await dispatcher.dispatch(query, context=context, limit=limit)
            if matches:
                last_matches = matches
                if matches[0].confidence >= self._threshold:
                    return matches
        return last_matches
