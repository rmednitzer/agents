"""Fifteenth audit, retrieval findings: BL-257, BL-258.

- BL-257: ``fuse_rrf`` ranks by DISTINCT position, so a duplicate id
  earlier in a list does not penalise the RRF score of the unique ids
  after it.
- BL-258: ``InMemorySemanticStore.query_hybrid`` rejects a Reranker that
  returns a non-finite score (the BL-159 / BL-221 non-finite class, the
  same guard ``tiering.demote_to_capacity`` applies to its rank scores).
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from memory.retrieval import fuse_rrf
from memory.semantic import InMemorySemanticStore
from memory.types import Namespace
from skills.embedding_providers import HashingEmbeddingProvider

# --- BL-257: fuse_rrf distinct-rank --------------------------------------


def test_fuse_rrf_duplicate_does_not_penalise_later_unique_rank() -> None:
    fused = dict(fuse_rrf(["a", "a", "b"], k=60))
    # 'a' is the first distinct id (rank 0); 'b' is the SECOND distinct
    # id (rank 1), not rank 2: the skipped duplicate must not advance it.
    assert fused["a"] == pytest.approx(1 / 60)
    assert fused["b"] == pytest.approx(1 / 61)


def test_fuse_rrf_no_duplicates_unchanged() -> None:
    fused = dict(fuse_rrf(["a", "b", "c"], k=60))
    assert fused["a"] == pytest.approx(1 / 60)
    assert fused["b"] == pytest.approx(1 / 61)
    assert fused["c"] == pytest.approx(1 / 62)


# --- BL-258: reranker non-finite guard -----------------------------------


class _NaNReranker:
    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        return [float("nan")] * len(documents)


def _store() -> InMemorySemanticStore:
    ns = Namespace(name="ns", workload="w")
    return InMemorySemanticStore(ns, HashingEmbeddingProvider(dim=128))


async def test_query_hybrid_rejects_nonfinite_reranker_score() -> None:
    s = _store()
    await s.write_semantic("a", b"A", text="alpha beta")
    await s.write_semantic("b", b"B", text="alpha gamma")
    with pytest.raises(ValueError, match="non-finite score"):
        await s.query_hybrid("alpha", k=2, reranker=_NaNReranker())
