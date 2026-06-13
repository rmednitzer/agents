"""Hybrid retrieval fusion and reranking (BL-243).

Vector-only similarity (``InMemorySemanticStore.query_semantic``,
BL-131) misses lexical matches a keyword index would catch, and a
keyword index misses paraphrase a vector catches. This module ships the
*fusion* layer the workload was previously left to assemble itself
(LIMITATIONS L5): a deterministic, dependency-free Reciprocal Rank
Fusion over any number of ranked id lists, a deterministic lexical
recall baseline, and an optional ``Reranker`` Protocol for a vendor
cross-encoder.

The split mirrors the ``Embedder`` stance (ADR 0001): the algorithm
(RRF) is in tree and deterministic, the models (the embedder and the
optional reranker) are injected so the framework binds no vendor.
``fuse_rrf`` and ``lexical_overlap_scores`` are pure functions;
``HybridSemanticStore`` is the extension Protocol the in-tree
``InMemorySemanticStore`` satisfies with ``query_hybrid``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from memory.store import SemanticMemoryStore

__all__ = [
    "HybridHit",
    "HybridSemanticStore",
    "Reranker",
    "fuse_rrf",
    "lexical_overlap_scores",
]


@dataclass(frozen=True)
class HybridHit:
    """One result of a hybrid (fused) retrieval query (BL-243).

    Unlike ``SemanticHit``, whose ``score`` is cosine in ``[-1, 1]``, a
    ``HybridHit`` ``score`` is the fused rank score (the RRF sum, a small
    positive number) or, when a ``Reranker`` ran, that reranker's
    relevance score. It is a ranking signal for ordering, not a
    calibrated similarity. ``value`` is the stored payload bytes for
    ``key``.
    """

    key: str
    score: float
    value: bytes


@runtime_checkable
class Reranker(Protocol):
    """Scores query/document relevance for a rerank stage (BL-243).

    The cross-encoder analogue of ``Embedder``: the model is injected so
    the framework binds no vendor (ADR 0001). ``rerank`` returns one
    score per document, in input order; a higher score means more
    relevant. A deterministic lexical reranker satisfies this Protocol
    and is the dependency-free option; a model-quality cross-encoder
    (a FlashRank / TinyBERT reranker) satisfies the same Protocol and is
    the workload's out-of-tree choice.
    """

    async def rerank(self, query: str, documents: Sequence[str]) -> Sequence[float]: ...


def fuse_rrf(*rankings: Sequence[str], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion of several ranked id lists (BL-243).

    Each ranking is an id list, best first. An id's fused score is the
    sum, over the lists it appears in, of ``1 / (k + rank)`` (``rank``
    0-based), the standard RRF with the conventional ``k = 60`` damping
    that bounds any single list's contribution and makes the fusion
    robust to one list's score scale. An id absent from a list
    contributes nothing for that list; a duplicate id within one list
    counts only at its first (best) position. Returns ``(id, score)``
    sorted by descending score, ties broken by id for determinism.
    ``k`` must be positive (it is a rank denominator).
    """
    if k <= 0:
        raise ValueError("k must be positive")
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, key in enumerate(ranking):
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def lexical_overlap_scores(query: str, documents: Sequence[str]) -> list[float]:
    """Deterministic token-overlap relevance, the in-tree keyword baseline.

    A dependency-free lexical scorer (the ``HashingEmbeddingProvider``
    stance, BL-110): a document's score is the count of distinct
    lowercased whitespace tokens it shares with the query, so a keyword
    recall pass needs no FTS engine. Returns one score per document, in
    input order. A model-quality keyword index (SQLite FTS5 bm25)
    satisfies the same role and is the workload's choice; this baseline
    keeps the in-tree hybrid path deterministic and network-free.
    """
    q_tokens = {t for t in query.lower().split() if t}
    if not q_tokens:
        return [0.0] * len(documents)
    return [float(len(q_tokens & {t for t in doc.lower().split() if t})) for doc in documents]


@runtime_checkable
class HybridSemanticStore(SemanticMemoryStore, Protocol):
    """A SemanticMemoryStore that also offers fused hybrid retrieval (BL-243).

    A *separate* extension Protocol over ``SemanticMemoryStore`` (the
    ADR 0004 "don't fake it" pattern): a backend implements it only if
    it can run a keyword pass beside the vector pass and fuse them.
    ``query_hybrid`` returns up to ``k`` ``HybridHit`` results ranked by
    the fused order (and the optional ``Reranker``); vector-only
    retrieval stays ``query_semantic``. Expired and deleted keys are
    excluded, the same TTL semantics as the core surface.
    """

    async def query_hybrid(
        self,
        text: str,
        *,
        k: int = 5,
        reranker: Reranker | None = None,
    ) -> list[HybridHit]: ...
