"""Tests for BL-131: SemanticMemoryStore Protocol + in-memory reference.

Uses skills.HashingEmbeddingProvider (BL-110) as the Embedder to show
the reuse intent: memory does not import skills, the provider just
structurally satisfies memory.semantic.Embedder.
"""

from __future__ import annotations

import asyncio

import pytest

from memory.semantic import Embedder, InMemorySemanticStore, _cosine
from memory.store import MemoryStore, SemanticMemoryStore
from memory.types import Namespace
from skills.embedding_providers import HashingEmbeddingProvider


def _store(retention: float | None = None) -> InMemorySemanticStore:
    ns = Namespace(name="ns", workload="w", retention_seconds=retention)
    return InMemorySemanticStore(ns, HashingEmbeddingProvider(dim=128))


def test_satisfies_protocols() -> None:
    s = _store()
    assert isinstance(s, MemoryStore)
    assert isinstance(s, SemanticMemoryStore)
    assert isinstance(HashingEmbeddingProvider(), Embedder)


@pytest.mark.asyncio
async def test_core_surface_roundtrips() -> None:
    s = _store()
    await s.write("k", b"v")
    assert await s.read("k") == b"v"
    assert await s.list_keys() == ["k"]
    await s.delete("k")
    assert await s.read("k") is None


@pytest.mark.asyncio
async def test_query_ranks_by_similarity() -> None:
    s = _store()
    await s.write_semantic("py", b"PY", text="python programming language code")
    await s.write_semantic("cook", b"CK", text="cooking recipes kitchen food")
    await s.write_semantic("snake", b"SN", text="python snake reptile animal")

    hits = await s.query_semantic("python code script", k=3)
    assert [h.key for h in hits][:1] == ["py"]
    assert hits[0].value == b"PY"
    # Scores are sorted descending and bounded.
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(-1.0 <= sc <= 1.0 for sc in scores)


@pytest.mark.asyncio
async def test_k_bounds_results_and_nonpositive_k_is_empty() -> None:
    s = _store()
    for i in range(5):
        await s.write_semantic(f"k{i}", b"v", text=f"topic number {i}")
    assert len(await s.query_semantic("topic", k=2)) == 2
    assert await s.query_semantic("topic", k=0) == []


@pytest.mark.asyncio
async def test_deleted_key_leaves_no_dangling_vector() -> None:
    s = _store()
    await s.write_semantic("a", b"A", text="alpha")
    await s.write_semantic("b", b"B", text="beta")
    await s.delete("a")
    hits = await s.query_semantic("alpha beta", k=5)
    assert [h.key for h in hits] == ["b"]


@pytest.mark.asyncio
async def test_expired_key_excluded_and_index_pruned() -> None:
    s = _store()
    await s.write_semantic("short", b"S", text="ephemeral", ttl_seconds=0.01)
    await s.write_semantic("long", b"L", text="durable", ttl_seconds=3600)
    await asyncio.sleep(0.02)
    hits = await s.query_semantic("ephemeral durable", k=5)
    assert [h.key for h in hits] == ["long"]
    # A second query confirms the expired vector was pruned, not just
    # filtered (no resurrection).
    assert [h.key for h in await s.query_semantic("ephemeral", k=5)] == ["long"]


@pytest.mark.asyncio
async def test_plain_write_invalidates_stale_vector() -> None:
    s = _store()
    await s.write_semantic("k", b"orig", text="machine learning")
    # A non-semantic overwrite must not keep ranking the old text.
    await s.write("k", b"new")
    assert await s.query_semantic("machine learning", k=5) == []
    assert await s.read("k") == b"new"


def test_cosine_guards_non_finite() -> None:
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert _cosine([float("nan"), 1.0], [1.0, 1.0]) == 0.0
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="dimension mismatch"):
        _cosine([1.0], [1.0, 2.0])
