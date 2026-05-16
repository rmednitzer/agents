"""KeywordDispatcher: deterministic scoring by triggers and token overlap."""

from __future__ import annotations

import re
from typing import Any

from skills.registry import SkillRegistry
from skills.types import Skill, SkillMatch

__all__ = ["KeywordDispatcher"]

_TOKEN_PATTERN = re.compile(r"\w+")


class KeywordDispatcher:
    """Deterministic dispatcher scoring on metadata triggers + description.

    Scoring:
    - +1.0 per trigger (from skill.metadata['triggers']) found in the
      query (case-insensitive substring match).
    - +0.1 per token shared between the query and the skill description
      (lowercased word boundary tokens).

    Skills with score 0 are excluded. Returned confidence is the score
    normalized to the top scorer's score, clamped to [0, 1].
    """

    name: str = "keyword"

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        if limit <= 0:
            return []
        query_lower = query.lower()
        query_tokens = set(_TOKEN_PATTERN.findall(query_lower))

        scored: list[tuple[float, Skill, str]] = []
        for skill in self._registry.all():
            score = 0.0
            hit_triggers: list[str] = []
            for trigger in skill.triggers:
                if trigger and trigger in query_lower:
                    score += 1.0
                    hit_triggers.append(trigger)

            desc_tokens = set(_TOKEN_PATTERN.findall(skill.description.lower()))
            overlap = query_tokens & desc_tokens
            score += 0.1 * len(overlap)

            if score <= 0:
                continue

            rationale_parts: list[str] = []
            if hit_triggers:
                rationale_parts.append(f"triggers: {', '.join(sorted(hit_triggers))}")
            if overlap:
                rationale_parts.append(f"description tokens: {', '.join(sorted(overlap))}")
            scored.append((score, skill, "; ".join(rationale_parts) or "match"))

        if not scored:
            return []

        scored.sort(key=lambda item: item[0], reverse=True)
        top_score = scored[0][0]
        return [
            SkillMatch(
                skill_name=skill.name,
                confidence=min(1.0, score / top_score) if top_score > 0 else 0.0,
                rationale=rationale,
                dispatcher=self.name,
            )
            for score, skill, rationale in scored[:limit]
        ]
