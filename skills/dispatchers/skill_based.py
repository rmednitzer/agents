"""SkillBasedDispatcher: dispatcher logic lives in a SKILL.md."""

from __future__ import annotations

import json
import math
from typing import Any

from harness.runtime import Runtime
from skills.dispatchers._json import first_json_array
from skills.errors import DispatchError, SkillError
from skills.registry import SkillRegistry
from skills.types import Skill, SkillMatch

__all__ = ["SkillBasedDispatcher"]


class SkillBasedDispatcher:
    """Routes via a markdown skill whose body is the dispatcher prompt.

    Useful when routing logic is large enough to deserve its own
    versioned artifact. The dispatcher loads the named skill, prepends
    its body to a query + catalog prompt, sends the result to the
    Runtime, and parses the response the same way LLMDispatcher does.

    Construction validates that the dispatcher_skill exists in the
    registry; otherwise raises SkillError immediately.
    """

    name: str = "skill-based"

    def __init__(
        self,
        registry: SkillRegistry,
        dispatcher_skill: str,
        runtime: Runtime,
    ) -> None:
        resolved = registry.get(dispatcher_skill)
        if resolved is None:
            raise SkillError(f"dispatcher_skill {dispatcher_skill!r} not in registry")
        self._registry = registry
        self._dispatcher_skill_name = dispatcher_skill
        # registry.all() yields bare names; dispatcher_skill may be a
        # name@version spec, so exclude by the resolved bare name to
        # keep the routing catalog free of the router skill (BL-053).
        self._dispatcher_skill_bare = resolved.name
        self._runtime = runtime

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        if limit <= 0:
            return []
        dispatcher_skill = self._registry.get(self._dispatcher_skill_name)
        if dispatcher_skill is None:
            return []

        # The dispatcher skill's body is the routing instructions.
        routing_instructions = dispatcher_skill.body()

        # Catalog excludes the dispatcher skill itself (by bare name,
        # since dispatcher_skill may be a name@version spec).
        candidates = [s for s in self._registry.all() if s.name != self._dispatcher_skill_bare]
        if not candidates:
            return []

        prompt = self._build_prompt(query, routing_instructions, candidates, limit)
        result = await self._runtime.run(prompt=prompt)
        return self._parse_response(result, limit, candidates)

    def _build_prompt(
        self,
        query: str,
        routing_instructions: str,
        candidates: list[Skill],
        limit: int,
    ) -> str:
        catalog = "\n".join(f"- {s.name}: {s.description}" for s in candidates)
        return (
            f"{routing_instructions}\n\n"
            f"Query: {query}\n\n"
            f"Catalog:\n{catalog}\n\n"
            f"Return a JSON array of up to {limit} objects, each with "
            "skill_name (string), confidence (0..1), rationale (string)."
        )

    def _parse_response(
        self,
        raw: Any,
        limit: int,
        candidates: list[Skill],
    ) -> list[SkillMatch]:
        text = raw if isinstance(raw, str) else json.dumps(raw)
        payload = first_json_array(text)
        if payload is None:
            raise DispatchError(f"skill-based dispatcher response missing JSON array: {text[:200]}")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise DispatchError(f"skill-based dispatcher JSON malformed: {exc}") from exc
        except RecursionError as exc:
            # Pathologically deep blob past first_json_array's candidate
            # cap reaches here as the parse-error fallback; keep the
            # DispatchError contract instead of escaping RecursionError.
            raise DispatchError("skill-based dispatcher JSON nesting is too deep") from exc
        if not isinstance(data, list):
            raise DispatchError("response root must be a JSON array")

        valid_names = {s.name for s in candidates}
        matches: list[SkillMatch] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            skill_name = entry.get("skill_name")
            confidence = entry.get("confidence")
            rationale = entry.get("rationale", "")
            if (
                not isinstance(skill_name, str)
                or isinstance(confidence, bool)
                or not isinstance(confidence, int | float)
                or not math.isfinite(confidence)
            ):
                # json.loads accepts NaN/Infinity; an unguarded NaN
                # survives max(0.0, min(1.0, nan)) as 1.0, the same
                # clamp trap as the cosine guard. Reject non-finite.
                continue
            if skill_name not in valid_names:
                continue
            matches.append(
                SkillMatch(
                    skill_name=skill_name,
                    confidence=max(0.0, min(1.0, float(confidence))),
                    rationale=str(rationale),
                    dispatcher=self.name,
                )
            )
        return matches[:limit]
