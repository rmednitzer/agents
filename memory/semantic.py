"""In-memory SemanticMemoryStore reference implementation (BL-131).

ADR 0004 keeps the core MemoryStore scalar key-value; semantic
retrieval is an additive extension Protocol (memory.store.
SemanticMemoryStore), shipped here with one deterministic reference
backend so retrieval-augmented and just-in-time-context workloads have
an in-tree option (LIMITATIONS L5).

The embedding model is injected (the ``Embedder`` Protocol), so the
framework binds no vendor (ADR 0001). ``skills.HashingEmbeddingProvider``
(BL-110) structurally satisfies ``Embedder`` and is the dependency-free
default; a model-quality embedder is the workload's choice. memory does
not import skills (the layering stays one-way); ``Embedder`` is memory's
own minimal structural Protocol.

This reference also satisfies ``memory.retrieval.HybridSemanticStore``
(BL-243): ``query_hybrid`` fuses the vector pass with a deterministic
lexical pass via Reciprocal Rank Fusion and an optional injected
``Reranker``, the in-tree answer to the vector-only quality gap
(LIMITATIONS L5). The fusion is deterministic and dependency-free; the
embedder and the optional reranker are the pluggable models.

Decorator forwarding (wrap_acl / wrap_encrypted) of the semantic
surface is intentionally out of scope here, exactly as BL-072 shipped
the CAS Protocol plus the InMemory reference before per-adapter impls:
encryption would have to embed plaintext while storing ciphertext and
ACL would have to gate a similarity query, each a separate decision.
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from harness.sinks import EventSink
from memory.inmemory import InMemoryStore
from memory.retrieval import HybridHit, Reranker, fuse_rrf, lexical_overlap_scores
from memory.store import SemanticHit
from memory.types import Namespace
from memory.validators import validate_key

__all__ = ["Embedder", "InMemorySemanticStore"]


@runtime_checkable
class Embedder(Protocol):
    """Maps texts to vectors. ``skills.HashingEmbeddingProvider`` fits.

    One unit-or-arbitrary-norm vector per input text, in order, all the
    same width. The store cosine-normalises, so the magnitude is
    irrelevant.
    """

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity with the BL-159 non-finite guard.

    Returns 0.0 for a zero or non-finite norm/score so a NaN component
    cannot survive ranking as a spuriously perfect 1.0 (the exact trap
    skills.embeddings.cosine_similarity closed).
    """
    if len(a) != len(b):
        raise ValueError("embedding dimension mismatch")
    dot = math.fsum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(math.fsum(x * x for x in a))
    nb = math.sqrt(math.fsum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    score = dot / (na * nb)
    if not math.isfinite(score):
        return 0.0
    return max(-1.0, min(1.0, score))


class InMemorySemanticStore:
    """InMemoryStore plus a deterministic vector index (BL-131).

    Core read/write/delete/list_keys delegate to an inner InMemoryStore
    so namespace isolation, key validation, TTL, lazy expiry, and the
    optional audit surface are inherited unchanged. ``write_semantic``
    additionally indexes the embedding of the supplied ``text`` (and
    retains the text itself for the BL-243 lexical pass);
    ``query_semantic`` embeds the query and returns the top-``k`` live
    hits by cosine similarity, ``query_hybrid`` fuses that with a
    lexical pass. A key's vector and text are dropped when the key is
    deleted or found expired, so neither index returns a stale or
    dangling hit.
    """

    name: str = "in-memory-semantic"

    def __init__(
        self,
        namespace: Namespace,
        embedder: Embedder,
        *,
        sink: EventSink | None = None,
        base_event_fields: dict[str, object] | None = None,
    ) -> None:
        self._inner = InMemoryStore(namespace, sink=sink, base_event_fields=base_event_fields)
        self._embedder = embedder
        self._vectors: dict[str, list[float]] = {}
        # Indexed source text per key, retained for the BL-243 lexical
        # recall pass and kept in lockstep with ``_vectors``.
        self._texts: dict[str, str] = {}

    @property
    def namespace(self) -> Namespace:
        return self._inner.namespace

    async def read(self, key: str) -> bytes | None:
        value = await self._inner.read(key)
        if value is None:
            # read() drops an expired entry; keep both indexes consistent.
            self._vectors.pop(key, None)
            self._texts.pop(key, None)
        return value

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        # A plain write replaces the value and invalidates any stale
        # vector/text for the key (the new value was not semantically
        # indexed); use write_semantic to (re-)index.
        await self._inner.write(key, value, ttl_seconds=ttl_seconds)
        self._vectors.pop(key, None)
        self._texts.pop(key, None)

    async def delete(self, key: str) -> None:
        await self._inner.delete(key)
        self._vectors.pop(key, None)
        self._texts.pop(key, None)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return await self._inner.list_keys(prefix)

    async def write_semantic(
        self,
        key: str,
        value: bytes,
        *,
        text: str,
        ttl_seconds: float | None = None,
    ) -> None:
        validate_key(key)
        vector = (await self._embedder.embed([text]))[0]
        await self._inner.write(key, value, ttl_seconds=ttl_seconds)
        self._vectors[key] = vector
        self._texts[key] = text

    async def query_semantic(self, text: str, *, k: int = 5) -> list[SemanticHit]:
        if k <= 0 or not self._vectors:
            return []
        query = (await self._embedder.embed([text]))[0]
        hits: list[SemanticHit] = []
        for key in list(self._vectors):
            value = await self._inner.read(key)
            # Read the vector via .get(), not [key]: another coroutine
            # may have run write()/delete() (popping the vector) while
            # this one was suspended at the await above. A concurrently
            # de-indexed key is simply skipped, not a KeyError that
            # aborts the whole query.
            vector = self._vectors.get(key)
            if value is None or vector is None:
                self._vectors.pop(key, None)
                self._texts.pop(key, None)
                continue
            hits.append(SemanticHit(key=key, score=_cosine(query, vector), value=value))
        # Descending similarity; ties broken by key for determinism.
        hits.sort(key=lambda h: (-h.score, h.key))
        return hits[:k]

    async def query_hybrid(
        self,
        text: str,
        *,
        k: int = 5,
        reranker: Reranker | None = None,
        rrf_k: int = 60,
    ) -> list[HybridHit]:
        """Hybrid retrieval: vector + lexical recall, RRF fusion, optional rerank.

        Runs the BL-131 vector pass and a deterministic lexical pass
        (``memory.retrieval.lexical_overlap_scores``) over the live
        indexed keys, fuses the two rankings with ``fuse_rrf``, and (when
        a ``Reranker`` is supplied) reorders the top candidates by the
        reranker's relevance score over a recall-then-rerank window.
        Returns up to ``k`` ``HybridHit`` results. Vector-only retrieval
        stays ``query_semantic``; this is the additive hybrid path
        (LIMITATIONS L5). Expired or concurrently de-indexed keys are
        pruned from both indexes and skipped, as in ``query_semantic``.
        ``rrf_k`` is the RRF rank-damping constant.
        """
        if rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        if k <= 0 or not self._vectors:
            return []
        query_vec = (await self._embedder.embed([text]))[0]
        # Gather the live, still-indexed keys with value, vector, and
        # indexed text. read() can suspend, so a concurrent write/delete
        # may de-index a key; .get() + skip mirrors query_semantic.
        live_keys: list[str] = []
        values: dict[str, bytes] = {}
        vectors: dict[str, list[float]] = {}
        texts: dict[str, str] = {}
        for key in list(self._vectors):
            value = await self._inner.read(key)
            vector = self._vectors.get(key)
            doc_text = self._texts.get(key)
            if value is None or vector is None or doc_text is None:
                self._vectors.pop(key, None)
                self._texts.pop(key, None)
                continue
            live_keys.append(key)
            values[key] = value
            vectors[key] = vector
            texts[key] = doc_text
        if not live_keys:
            return []
        # Vector pass: cosine descending, ties by key.
        vector_ranking = sorted(
            live_keys, key=lambda candidate: (-_cosine(query_vec, vectors[candidate]), candidate)
        )
        # Lexical pass: a keyword hit list (token overlap > 0), descending.
        lexical_scores = lexical_overlap_scores(text, [texts[key] for key in live_keys])
        lexical_ranking = [
            key
            for key, score in sorted(
                zip(live_keys, lexical_scores, strict=True),
                key=lambda pair: (-pair[1], pair[0]),
            )
            if score > 0.0
        ]
        fused = fuse_rrf(vector_ranking, lexical_ranking, k=rrf_k)
        if reranker is None:
            return [HybridHit(key=key, score=score, value=values[key]) for key, score in fused[:k]]
        # Recall-then-rerank: rerank only the top fused candidates.
        window = min(len(fused), max(k * 3, 30))
        candidates = [key for key, _ in fused[:window]]
        scores = await reranker.rerank(text, [texts[key] for key in candidates])
        if len(scores) != len(candidates):
            raise ValueError(
                f"reranker returned {len(scores)} scores for {len(candidates)} documents"
            )
        reranked = sorted(
            zip(candidates, scores, strict=True), key=lambda pair: (-pair[1], pair[0])
        )
        return [
            HybridHit(key=key, score=float(score), value=values[key]) for key, score in reranked[:k]
        ]
