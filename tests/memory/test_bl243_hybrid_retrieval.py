"""BL-243: hybrid retrieval (RRF fusion + reranker) over the semantic store.

Covers:

- fuse_rrf: single-list order, multi-list fusion, the 1/(k+rank)
  scoring, dedupe within a list, descending order with lexicographic
  ties, empty input, non-positive k rejected;
- lexical_overlap_scores: distinct-token overlap counts,
  case-insensitivity, empty query;
- InMemorySemanticStore.query_hybrid: satisfies HybridSemanticStore,
  fuses a vector pass with a lexical pass so a pure keyword match
  surfaces, k bounds, de-index pruning, and the optional Reranker
  reorders (a wrong-length score list raises).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from memory.retrieval import (
    HybridHit,
    HybridSemanticStore,
    Reranker,
    fuse_rrf,
    lexical_overlap_scores,
)
from memory.semantic import InMemorySemanticStore
from memory.types import Namespace
from skills.embedding_providers import HashingEmbeddingProvider


def _store(retention: float | None = None) -> InMemorySemanticStore:
    ns = Namespace(name="ns", workload="w", retention_seconds=retention)
    return InMemorySemanticStore(ns, HashingEmbeddingProvider(dim=128))


class _PositionReranker:
    """Deterministic reranker: scores ascending by candidate position.

    The last candidate gets the highest score, so the rerank stage
    yields the reverse of the fused candidate order, an observable
    reorder with no model dependency.
    """

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        return [float(i) for i in range(len(documents))]


class _ShortReranker:
    """Misbehaving reranker that returns too few scores."""

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        return [1.0]


# --- fuse_rrf ------------------------------------------------------------


def test_fuse_rrf_single_list_preserves_order() -> None:
    fused = fuse_rrf(["a", "b", "c"], k=60)
    assert [key for key, _ in fused] == ["a", "b", "c"]
    scores = [s for _, s in fused]
    assert scores[0] == pytest.approx(1 / 60)
    assert scores[1] == pytest.approx(1 / 61)
    assert scores == sorted(scores, reverse=True)


def test_fuse_rrf_combines_lists() -> None:
    # 'b' appears in both lists, so it accumulates both contributions and
    # outranks 'a' (first list only) and 'c' (second list only).
    fused = dict(fuse_rrf(["a", "b"], ["b", "c"], k=60))
    assert fused["b"] == pytest.approx(1 / 61 + 1 / 60)
    assert fused["a"] == pytest.approx(1 / 60)
    assert fused["c"] == pytest.approx(1 / 61)
    assert fuse_rrf(["a", "b"], ["b", "c"], k=60)[0][0] == "b"


def test_fuse_rrf_dedupes_within_list() -> None:
    fused = dict(fuse_rrf(["a", "a", "b"], k=60))
    assert fused["a"] == pytest.approx(1 / 60)  # first position wins, dup ignored


def test_fuse_rrf_ties_broken_by_id() -> None:
    assert [key for key, _ in fuse_rrf(["b"], ["a"], k=60)] == ["a", "b"]


def test_fuse_rrf_empty_and_bad_k() -> None:
    assert fuse_rrf() == []
    assert fuse_rrf([], []) == []
    with pytest.raises(ValueError, match="must be positive"):
        fuse_rrf(["a"], k=0)


# --- lexical_overlap_scores ---------------------------------------------


def test_lexical_overlap_counts_distinct_shared_tokens() -> None:
    scores = lexical_overlap_scores("python code", ["python python code", "cooking food", ""])
    assert scores == [2.0, 0.0, 0.0]


def test_lexical_overlap_case_insensitive_and_empty_query() -> None:
    assert lexical_overlap_scores("PYTHON", ["python"]) == [1.0]
    assert lexical_overlap_scores("", ["anything", "here"]) == [0.0, 0.0]


# --- query_hybrid --------------------------------------------------------


def test_satisfies_hybrid_protocol() -> None:
    assert isinstance(_store(), HybridSemanticStore)
    assert isinstance(_PositionReranker(), Reranker)


@pytest.mark.asyncio
async def test_query_hybrid_returns_hybrid_hits() -> None:
    s = _store()
    await s.write_semantic("py", b"PY", text="python programming language")
    await s.write_semantic("cook", b"CK", text="cooking recipes kitchen")
    hits = await s.query_hybrid("python programming", k=2)
    assert all(isinstance(h, HybridHit) for h in hits)
    assert hits[0].key == "py"
    assert hits[0].value == b"PY"
    assert hits[0].score > 0.0


@pytest.mark.asyncio
async def test_query_hybrid_surfaces_exact_keyword_match() -> None:
    # 'b' is the only document sharing the query token. It gains a
    # lexical RRF contribution no other document has, so it ranks first
    # regardless of the (hashing) embedder's vector order.
    s = _store()
    await s.write_semantic("a", b"A", text="alpha beta gamma")
    await s.write_semantic("b", b"B", text="zeta eta theta")
    await s.write_semantic("c", b"C", text="unrelated words here")
    hits = await s.query_hybrid("zeta", k=3)
    assert hits[0].key == "b"


@pytest.mark.asyncio
async def test_query_hybrid_k_bounds_and_empty() -> None:
    s = _store()
    assert await s.query_hybrid("x", k=3) == []  # nothing indexed
    await s.write_semantic("a", b"A", text="alpha")
    assert await s.query_hybrid("alpha", k=0) == []
    assert await s.query_hybrid("alpha", k=-1) == []
    assert len(await s.query_hybrid("alpha", k=1)) == 1


@pytest.mark.asyncio
async def test_query_hybrid_drops_deindexed_keys() -> None:
    s = _store()
    await s.write_semantic("a", b"A", text="alpha beta")
    await s.write("a", b"A2")  # plain write de-indexes the key
    assert await s.query_hybrid("alpha", k=5) == []
    await s.write_semantic("b", b"B", text="alpha gamma")
    await s.delete("b")
    assert await s.query_hybrid("alpha", k=5) == []


@pytest.mark.asyncio
async def test_query_hybrid_reranker_reorders() -> None:
    s = _store()
    await s.write_semantic("a", b"A", text="alpha one")
    await s.write_semantic("b", b"B", text="alpha two")
    await s.write_semantic("c", b"C", text="alpha three")
    fused = [h.key for h in await s.query_hybrid("alpha", k=3)]
    reranked = [h.key for h in await s.query_hybrid("alpha", k=3, reranker=_PositionReranker())]
    assert reranked == list(reversed(fused))


@pytest.mark.asyncio
async def test_query_hybrid_reranker_length_mismatch_raises() -> None:
    s = _store()
    await s.write_semantic("a", b"A", text="alpha")
    await s.write_semantic("b", b"B", text="alpha beta")
    with pytest.raises(ValueError, match="reranker returned"):
        await s.query_hybrid("alpha", k=2, reranker=_ShortReranker())


@pytest.mark.asyncio
async def test_query_hybrid_excludes_and_prunes_expired() -> None:
    s = _store()
    await s.write_semantic("short", b"S", text="alpha ephemeral", ttl_seconds=0.01)
    await s.write_semantic("long", b"L", text="alpha durable", ttl_seconds=3600)
    await asyncio.sleep(0.02)
    hits = await s.query_hybrid("alpha", k=5)
    assert [h.key for h in hits] == ["long"]
    assert "short" not in s._vectors  # the expired entry was pruned, not just filtered
    # When every indexed key has expired, the gather yields no live keys.
    s2 = _store()
    await s2.write_semantic("only", b"O", text="alpha", ttl_seconds=0.01)
    await asyncio.sleep(0.02)
    assert await s2.query_hybrid("alpha", k=5) == []
