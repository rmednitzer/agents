"""BL-222 (eighth audit, ADR 0018): MultiDispatcher member-failure
robustness.

Class extension of BL-207 / BL-208 (InstrumentedDispatcher telemetry on
failure) on the ensemble side. The default `asyncio.gather()` cancels
sibling tasks on the first exception, so a single flaky member (an
LLM-backed inner that raises `DispatchError` on a malformed response,
an embedding provider that times out) crashed the whole ensemble. As a
secondary effect, the cancelled siblings' `InstrumentedDispatcher`
`try/finally` wrappers (BL-207) then emitted `fell_back=True / matched=0`
events; cancellation was indistinguishable from a real fallback in the
routing-health telemetry.

The fix is `return_exceptions=True` in the gather call plus a skip on
exceptional results in the aggregation loop. Surviving members
contribute truthfully; the exception is contained at the ensemble
boundary instead of poisoning every observer.
"""

from __future__ import annotations

from typing import Any

import pytest

from skills.dispatcher import Dispatcher
from skills.dispatchers.multi import MultiDispatcher, MultiMode
from skills.types import SkillMatch


class _StaticDispatcher:
    """A Dispatcher that returns a fixed list."""

    def __init__(self, name: str, matches: list[SkillMatch]) -> None:
        self.name = name
        self._matches = matches

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        return list(self._matches[:limit])


class _FailingDispatcher:
    """A Dispatcher that raises on every call."""

    name = "failing"

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        raise RuntimeError("simulated member failure")


def _match(skill: str, confidence: float, dispatcher: str = "x") -> SkillMatch:
    return SkillMatch(
        skill_name=skill,
        confidence=confidence,
        rationale="t",
        dispatcher=dispatcher,
    )


async def test_one_failing_member_does_not_crash_ensemble() -> None:
    good_a = _StaticDispatcher("a", [_match("alpha", 0.9), _match("beta", 0.5)])
    bad = _FailingDispatcher()
    good_b = _StaticDispatcher("b", [_match("alpha", 0.7)])

    multi: Dispatcher = MultiDispatcher([good_a, bad, good_b], mode=MultiMode.AVERAGE)
    out = await multi.dispatch("query", limit=2)

    # The two healthy members both ranked "alpha" at the top; the
    # failing member is silently dropped (its contribution is 0).
    assert out, "expected at least one match from the surviving members"
    assert out[0].skill_name == "alpha"


async def test_failure_recorded_as_zero_contribution_in_average() -> None:
    # AVERAGE divides by `n` (total members), not by surviving members,
    # so a failing member's contribution is 0/n. This preserves the
    # documented "a member that did not return the skill contributes 0"
    # semantic and treats failure the same as no-return.
    good = _StaticDispatcher("g", [_match("alpha", 1.0)])
    bad = _FailingDispatcher()
    multi = MultiDispatcher([good, bad], mode=MultiMode.AVERAGE)
    out = await multi.dispatch("q", limit=1)
    assert out[0].skill_name == "alpha"
    # (1.0 + 0) / 2 = 0.5
    assert out[0].confidence == pytest.approx(0.5)


async def test_all_members_fail_returns_empty() -> None:
    bad1 = _FailingDispatcher()
    bad2 = _FailingDispatcher()
    multi = MultiDispatcher([bad1, bad2])
    out = await multi.dispatch("q", limit=1)
    assert out == []


async def test_happy_path_unchanged() -> None:
    # Sanity: with no failures, behaviour is byte-for-byte the same
    # as before BL-222.
    a = _StaticDispatcher("a", [_match("alpha", 0.8)])
    b = _StaticDispatcher("b", [_match("alpha", 0.6)])
    multi = MultiDispatcher([a, b], mode=MultiMode.AVERAGE)
    out = await multi.dispatch("q", limit=1)
    assert len(out) == 1
    assert out[0].skill_name == "alpha"
    assert out[0].confidence == pytest.approx(0.7)
