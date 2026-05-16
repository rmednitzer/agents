"""Tests for EmbeddingDispatcher (BL-051) with a deterministic provider."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from skills.dispatchers.embedding import EmbeddingDispatcher
from skills.embeddings import cosine_similarity
from skills.registry import SkillRegistry
from skills.types import Skill, SkillManifest


class _BagOfWords:
    """Deterministic, network-free embedding over a fixed vocabulary."""

    _VOCAB: ClassVar[list[str]] = ["deploy", "ship", "database", "sql", "search", "index"]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            low = t.lower()
            out.append([float(low.count(w)) for w in self._VOCAB])
        return out


def _skill(name: str, description: str) -> Skill:
    return Skill(
        manifest=SkillManifest(name=name, description=description),
        path=Path("/tmp") / name,
    )


def test_cosine_similarity_basics() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    with pytest.raises(ValueError, match="dimension"):
        cosine_similarity([1.0], [1.0, 2.0])


@pytest.mark.asyncio
async def test_ranks_by_similarity() -> None:
    r = SkillRegistry()
    r.add(_skill("deployer", "deploy and ship the application"))
    r.add(_skill("dba", "database sql index maintenance"))
    d = EmbeddingDispatcher(r, _BagOfWords())

    (top,) = await d.dispatch("please deploy and ship", limit=1)
    assert top.skill_name == "deployer"
    assert 0.0 <= top.confidence <= 1.0

    (top2,) = await d.dispatch("optimize the sql database index", limit=1)
    assert top2.skill_name == "dba"


@pytest.mark.asyncio
async def test_empty_registry_and_zero_limit() -> None:
    assert await EmbeddingDispatcher(SkillRegistry(), _BagOfWords()).dispatch("q") == []
    r = SkillRegistry()
    r.add(_skill("x", "deploy"))
    assert await EmbeddingDispatcher(r, _BagOfWords()).dispatch("q", limit=0) == []


@pytest.mark.asyncio
async def test_skill_vectors_cached() -> None:
    calls: list[int] = []

    class _Counting(_BagOfWords):
        async def embed(self, texts: list[str]) -> list[list[float]]:
            calls.append(len(texts))
            return await super().embed(texts)

    r = SkillRegistry()
    r.add(_skill("a", "deploy"))
    r.add(_skill("b", "database"))
    d = EmbeddingDispatcher(r, _Counting())
    await d.dispatch("deploy")
    await d.dispatch("database")
    # First call embeds 2 skill descriptions once, then 1 query each call.
    assert calls == [2, 1, 1]
