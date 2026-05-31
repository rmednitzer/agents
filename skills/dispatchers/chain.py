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

    Per-link failure is contained (``BL-228``, eleventh audit; the
    BL-222 / BL-223 / BL-227 fan-out containment class on the
    sequential cheap-first chain). A link that raises ``Exception``
    (a network ``LLMDispatcher`` raising ``DispatchError`` or timing
    out, an embedding provider error) is treated as "this link
    produced no usable match": the chain falls through to the next
    link, preserving the best-effort matches already gathered from
    cheaper links. This matches the best-effort-fallback contract
    above (a raising link is the failure analogue of a link that
    returned nothing). ``BaseException`` (``KeyboardInterrupt``,
    ``SystemExit``, ``asyncio.CancelledError``) is NOT contained:
    terminal signals must reach the caller, the BL-165 / BL-222 /
    BL-223 invariant.
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
            # BL-228: contain a per-link failure so a single flaky
            # dispatcher (a network LLMDispatcher, an embedding provider
            # blip) does not crash the whole chain. A raising link is
            # treated as "produced no usable match" and the chain falls
            # through to the next, preserving `last_matches` from the
            # cheaper links that already ran. `default_dispatcher`
            # (BL-103) composes a RoutingChainDispatcher, so an LLM-tier
            # failure on the recommended default path now degrades to
            # the keyword / embedding tier instead of surfacing as a
            # whole-dispatch crash. Only `Exception` is caught;
            # `BaseException` (the bare `except Exception` excludes it)
            # still propagates per the BL-165 / BL-222 / BL-223
            # terminal-signal invariant.
            try:
                matches = await dispatcher.dispatch(query, context=context, limit=limit)
            except Exception:
                continue
            if matches:
                last_matches = matches
                if matches[0].confidence >= self._threshold:
                    return matches
        return last_matches
