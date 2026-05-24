"""MultiDispatcher: ensemble over several dispatchers (BL-050)."""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from enum import StrEnum
from typing import Any

from skills.dispatcher import Dispatcher
from skills.types import SkillMatch

__all__ = ["MultiDispatcher", "MultiMode"]


class MultiMode(StrEnum):
    """How to combine the members' per-skill confidences.

    - VOTE: score = fraction of members that returned the skill at all;
      ties broken by mean confidence. Rewards consensus over magnitude.
    - AVERAGE: score = mean confidence across *all* members (a member
      that did not return the skill contributes 0), so a skill only one
      member loves cannot dominate.
    - WEIGHTED: score = sum(weight_i * confidence_i) / sum(weights),
      members that did not return the skill contributing 0.
    """

    VOTE = "vote"
    AVERAGE = "average"
    WEIGHTED = "weighted"


class MultiDispatcher:
    """Runs members concurrently and blends their SkillMatches.

    Members are queried with an expanded ``limit`` (members may disagree
    on the head, so a wider net before blending), then results are
    aggregated per skill by the chosen mode and the top ``limit``
    returned. Pure: no side effects, members are assumed pure too.
    """

    name: str = "multi"

    def __init__(
        self,
        members: list[Dispatcher],
        *,
        mode: MultiMode = MultiMode.AVERAGE,
        weights: list[float] | None = None,
        candidate_limit: int = 10,
    ) -> None:
        if not members:
            raise ValueError("MultiDispatcher requires at least one member")
        if weights is not None and len(weights) != len(members):
            raise ValueError("weights must align 1:1 with members")
        if mode == MultiMode.WEIGHTED and weights is None:
            raise ValueError("WEIGHTED mode requires weights")
        # Finite-and-non-negative guard on weights (`BL-205`, BL-159
        # class extension): the downstream score clamp
        # ``max(0.0, min(1.0, score))`` collapses a NaN weight to
        # confidence 1.0 (the exact BL-159 NaN-clamp trap fixed for
        # cosine_similarity / LLMDispatcher / SkillBasedDispatcher);
        # validating at construction surfaces the configuration bug at
        # the API boundary rather than silently shipping
        # confidence-1.0 noise.
        if weights is not None and not all(math.isfinite(w) and w >= 0 for w in weights):
            raise ValueError("weights must be finite and non-negative")
        self._members = members
        self._mode = mode
        self._weights = weights or [1.0] * len(members)
        self._candidate_limit = candidate_limit

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        if limit <= 0:
            return []
        # BL-222: a single flaky member (an LLM-backed inner that
        # raises `DispatchError` on a malformed response, an embedding
        # provider that times out) must not poison the ensemble. The
        # default `asyncio.gather()` cancels sibling tasks on the first
        # exception, and the cancelled siblings' `InstrumentedDispatcher`
        # `try/finally` wrappers (BL-207) then emit `fell_back=True /
        # matched=0` events, making cancellation indistinguishable
        # from a real fallback in the routing-health telemetry. Use
        # `return_exceptions=True` so each member runs to completion
        # (or its own failure); skip exceptional results in the
        # aggregation loop so the surviving members' contributions are
        # blended truthfully. BL-207 / BL-208 class extension on the
        # ensemble side: the ensemble's robustness is the dual of the
        # InstrumentedDispatcher's "observable on failure" guarantee.
        raw_results = await asyncio.gather(
            *(
                m.dispatch(query, context=context, limit=self._candidate_limit)
                for m in self._members
            ),
            return_exceptions=True,
        )
        results: list[list[SkillMatch]] = [
            r if isinstance(r, list) else [] for r in raw_results
        ]

        confidences: dict[str, list[float]] = defaultdict(list)
        weighted_sum: dict[str, float] = defaultdict(float)
        voters: dict[str, int] = defaultdict(int)
        for weight, matches in zip(self._weights, results, strict=True):
            for match in matches:
                confidences[match.skill_name].append(match.confidence)
                weighted_sum[match.skill_name] += weight * match.confidence
                voters[match.skill_name] += 1

        n = len(self._members)
        total_weight = sum(self._weights)
        scored: list[tuple[float, str]] = []
        for skill, confs in confidences.items():
            if self._mode == MultiMode.VOTE:
                score = voters[skill] / n
            elif self._mode == MultiMode.AVERAGE:
                score = sum(confs) / n
            else:  # WEIGHTED
                score = weighted_sum[skill] / total_weight if total_weight else 0.0
            scored.append((score, skill))

        # Tie-break by mean confidence over members that returned the
        # skill (matches the docstring and the rationale string).
        scored.sort(
            key=lambda t: (t[0], sum(confidences[t[1]]) / len(confidences[t[1]])),
            reverse=True,
        )
        return [
            SkillMatch(
                skill_name=skill,
                confidence=max(0.0, min(1.0, score)),
                rationale=(
                    f"{self._mode.value}: {voters[skill]}/{n} members, "
                    f"mean conf {sum(confidences[skill]) / len(confidences[skill]):.3f}"
                ),
                dispatcher=self.name,
            )
            for score, skill in scored[:limit]
        ]
