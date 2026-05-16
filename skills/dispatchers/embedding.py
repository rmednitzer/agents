"""EmbeddingDispatcher: vector similarity between query and skills (BL-051)."""

from __future__ import annotations

from typing import Any

from skills.embeddings import EmbeddingProvider, cosine_similarity
from skills.registry import SkillRegistry
from skills.types import SkillMatch

__all__ = ["EmbeddingDispatcher"]


class EmbeddingDispatcher:
    """Ranks skills by cosine similarity of query vs description vectors.

    Skill description vectors are computed once and cached (the registry
    is treated as static for the dispatcher's lifetime, matching
    SkillRegistry's eager-manifest model). Confidence is the cosine
    similarity rescaled from [-1, 1] to [0, 1] so it satisfies
    SkillMatch's validator and is comparable within this dispatcher.
    """

    name: str = "embedding"

    def __init__(self, registry: SkillRegistry, provider: EmbeddingProvider) -> None:
        self._registry = registry
        self._provider = provider
        self._cache: dict[str, list[float]] | None = None

    async def _skill_vectors(self) -> dict[str, list[float]]:
        if self._cache is None:
            skills = self._registry.all()
            if not skills:
                self._cache = {}
            else:
                vectors = await self._provider.embed([s.description for s in skills])
                self._cache = {s.name: v for s, v in zip(skills, vectors, strict=True)}
        return self._cache

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        if limit <= 0:
            return []
        vectors = await self._skill_vectors()
        if not vectors:
            return []
        (query_vec,) = await self._provider.embed([query])

        scored = sorted(
            ((cosine_similarity(query_vec, vec), name) for name, vec in vectors.items()),
            key=lambda t: t[0],
            reverse=True,
        )
        return [
            SkillMatch(
                skill_name=name,
                confidence=max(0.0, min(1.0, (sim + 1.0) / 2.0)),
                rationale=f"cosine similarity {sim:.4f}",
                dispatcher=self.name,
            )
            for sim, name in scored[:limit]
        ]
