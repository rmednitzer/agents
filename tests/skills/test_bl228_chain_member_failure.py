"""BL-228 (eleventh audit, ADR 0021): RoutingChainDispatcher per-link
failure containment.

Class extension of BL-222 (MultiDispatcher per-member failure
containment) / BL-223 (MultiSink per-sink containment) / BL-227 (S3
evict per-key containment) onto the sequential cheap-first chain. The
chain loop called ``await dispatcher.dispatch(...)`` with no exception
containment, so a single flaky link (a network ``LLMDispatcher``
raising ``DispatchError`` / timing out, an embedding provider blip)
crashed the whole chain and discarded the best-effort fallback the
chain documents, including the matches already gathered from cheaper
links that ran first. ``default_dispatcher`` (BL-103) composes a
``RoutingChainDispatcher``, so the failure mode was on the recommended
default routing path.

The fix contains ``Exception`` per link (a raising link is treated as
"produced no usable match" and the chain falls through to the next,
preserving ``last_matches``); ``BaseException`` (KeyboardInterrupt,
SystemExit, asyncio.CancelledError) still propagates per the BL-165 /
BL-222 / BL-223 terminal-signal invariant. This is the clear
fall-through-vs-propagate semantic the ADR 0019 / ADR 0020 revisit
trigger asked for.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from skills.dispatchers.chain import RoutingChainDispatcher
from skills.types import SkillMatch


class _StubDispatcher:
    """Returns a fixed list; records its call count."""

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


class _FailingDispatcher:
    """Raises ``Exception`` on every call (a flaky network link)."""

    name = "failing"

    def __init__(self) -> None:
        self.call_count = 0

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        self.call_count += 1
        raise RuntimeError("simulated link failure")


class _BaseExceptionDispatcher:
    """Raises a ``BaseException`` (must propagate, never contained)."""

    name = "terminal"

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        raise self._exc


def _match(name: str, confidence: float, dispatcher: str = "stub") -> SkillMatch:
    return SkillMatch(
        skill_name=name,
        confidence=confidence,
        rationale="",
        dispatcher=dispatcher,
    )


@pytest.mark.asyncio
async def test_failing_middle_link_falls_through_to_later_success() -> None:
    # cheap (below threshold) -> failing -> expensive (above threshold).
    # Pre-fix, the failing middle link crashed the whole dispatch. Now
    # the chain falls through and the expensive link wins.
    cheap = _StubDispatcher("cheap", [_match("x", 0.3)])
    bad = _FailingDispatcher()
    expensive = _StubDispatcher("expensive", [_match("y", 0.95)])
    chain = RoutingChainDispatcher([cheap, bad, expensive], threshold=0.6)
    matches = await chain.dispatch("q")
    assert matches[0].skill_name == "y"
    assert cheap.call_count == 1
    assert bad.call_count == 1
    assert expensive.call_count == 1


@pytest.mark.asyncio
async def test_failing_last_link_preserves_earlier_best_effort() -> None:
    # cheap returns a below-threshold match; the expensive fallback
    # raises. The chain must still return the cheap link's best-effort
    # match (last_matches), exactly as it would if the expensive link
    # had returned nothing.
    cheap = _StubDispatcher("cheap", [_match("x", 0.4)])
    bad = _FailingDispatcher()
    chain = RoutingChainDispatcher([cheap, bad], threshold=0.9)
    matches = await chain.dispatch("q")
    assert matches[0].skill_name == "x"
    assert matches[0].confidence == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_failing_first_link_does_not_abort_chain() -> None:
    # A failing first link must not prevent a later link from winning.
    bad = _FailingDispatcher()
    good = _StubDispatcher("good", [_match("y", 0.9)])
    chain = RoutingChainDispatcher([bad, good], threshold=0.6)
    matches = await chain.dispatch("q")
    assert matches[0].skill_name == "y"
    assert good.call_count == 1


@pytest.mark.asyncio
async def test_all_links_fail_returns_empty() -> None:
    # Symmetric to the all-empty case: every link erroring is the
    # failure analogue of every link returning nothing, so the chain
    # returns []. Parity with MultiDispatcher BL-222 all-fail.
    chain = RoutingChainDispatcher([_FailingDispatcher(), _FailingDispatcher()], threshold=0.6)
    matches = await chain.dispatch("q")
    assert matches == []


@pytest.mark.asyncio
async def test_failing_link_above_threshold_winner_still_short_circuits() -> None:
    # cheap wins above threshold; the later (would-be failing) link must
    # never be reached, so the contained failure cannot even occur.
    cheap = _StubDispatcher("cheap", [_match("x", 0.9)])
    bad = _FailingDispatcher()
    chain = RoutingChainDispatcher([cheap, bad], threshold=0.6)
    matches = await chain.dispatch("q")
    assert matches[0].skill_name == "x"
    assert bad.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [KeyboardInterrupt(), SystemExit(), asyncio.CancelledError()],
)
async def test_base_exception_propagates(exc: BaseException) -> None:
    # The containment catches `Exception`, not `BaseException`: a
    # terminal signal from a link must reach the caller (BL-165 /
    # BL-222 / BL-223 invariant), even when a healthy later link exists.
    terminal = _BaseExceptionDispatcher(exc)
    never = _StubDispatcher("never", [_match("y", 0.9)])
    chain = RoutingChainDispatcher([terminal, never], threshold=0.6)
    with pytest.raises(type(exc)):
        await chain.dispatch("q")
    assert never.call_count == 0


@pytest.mark.asyncio
async def test_happy_path_unchanged_by_containment() -> None:
    # Sanity: with no failures, behaviour is byte-for-byte the prior
    # cheap-first semantic (first above threshold wins, no later call).
    cheap = _StubDispatcher("cheap", [_match("x", 0.9)])
    expensive = _StubDispatcher("expensive", [_match("y", 1.0)])
    chain = RoutingChainDispatcher([cheap, expensive], threshold=0.6)
    matches = await chain.dispatch("q")
    assert matches[0].skill_name == "x"
    assert cheap.call_count == 1
    assert expensive.call_count == 0
