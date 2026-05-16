"""LLMDispatcher: uses a Runtime to select among candidate skills."""

from __future__ import annotations

import json
from typing import Any

from harness.runtime import Runtime
from skills.dispatchers._json import first_json_array
from skills.errors import DispatchError
from skills.registry import SkillRegistry
from skills.types import Skill, SkillMatch

__all__ = ["LLMDispatcher"]


class LLMDispatcher:
    """Uses a Runtime to choose the most relevant skill for a query.

    The dispatcher constructs a prompt enumerating up to max_candidates
    skills (name + description), then asks the runtime to return a JSON
    object with skill_name, confidence (0..1), and rationale. Up to
    `limit` matches are returned, sorted by confidence.

    For test stubs and deterministic scoring use KeywordDispatcher. The
    LLM dispatcher is the higher-cost fallback when keyword scoring is
    too ambiguous.
    """

    name: str = "llm"

    def __init__(
        self,
        registry: SkillRegistry,
        runtime: Runtime,
        *,
        max_candidates: int = 20,
    ) -> None:
        self._registry = registry
        self._runtime = runtime
        self._max_candidates = max_candidates

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        if limit <= 0:
            return []
        candidates: list[Skill] = self._registry.all()[: self._max_candidates]
        if not candidates:
            return []

        prompt = self._build_prompt(query, candidates, limit)
        result = await self._runtime.run(prompt=prompt)
        matches = self._parse_response(result, limit)
        return matches

    def _build_prompt(self, query: str, candidates: list[Skill], limit: int) -> str:
        catalog = "\n".join(f"- {skill.name}: {skill.description}" for skill in candidates)
        return (
            "You are a skill dispatcher. Given a user query and a "
            "catalog of skills, choose the most relevant skill(s).\n\n"
            f"Query: {query}\n\n"
            f"Catalog:\n{catalog}\n\n"
            f"Return a JSON array of up to {limit} objects, each with "
            "the keys: skill_name (string), confidence (number 0..1), "
            "rationale (short string). Order by descending confidence."
        )

    def _parse_response(self, raw: Any, limit: int) -> list[SkillMatch]:
        text = raw if isinstance(raw, str) else json.dumps(raw)
        json_payload = self._extract_json_array(text)
        if json_payload is None:
            raise DispatchError(f"LLM response did not contain a JSON array: {text[:200]}")
        try:
            data = json.loads(json_payload)
        except json.JSONDecodeError as exc:
            raise DispatchError(f"LLM response JSON is malformed: {exc}") from exc
        if not isinstance(data, list):
            raise DispatchError("LLM response root must be a JSON array")

        matches: list[SkillMatch] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            skill_name = entry.get("skill_name")
            confidence = entry.get("confidence")
            rationale = entry.get("rationale", "")
            if not isinstance(skill_name, str) or not isinstance(confidence, int | float):
                continue
            if self._registry.get(skill_name) is None:
                continue
            confidence_f = max(0.0, min(1.0, float(confidence)))
            matches.append(
                SkillMatch(
                    skill_name=skill_name,
                    confidence=confidence_f,
                    rationale=str(rationale),
                    dispatcher=self.name,
                )
            )
        return matches[:limit]

    @staticmethod
    def _extract_json_array(text: str) -> str | None:
        return first_json_array(text)
