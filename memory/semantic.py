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
    additionally indexes the embedding of the supplied ``text``;
    ``query_semantic`` embeds the query and returns the top-``k`` live
    hits by cosine similarity. A key's vector is dropped when the key is
    deleted or found expired, so the index never returns a stale or
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
        self._inner = InMemoryStore(
            namespace, sink=sink, base_event_fields=base_event_fields
        )
        self._embedder = embedder
        self._vectors: dict[str, list[float]] = {}

    @property
    def namespace(self) -> Namespace:
        return self._inner.namespace

    async def read(self, key: str) -> bytes | None:
        value = await self._inner.read(key)
        if value is None:
            # read() drops an expired entry; keep the index consistent.
            self._vectors.pop(key, None)
        return value

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        # A plain write replaces the value and invalidates any stale
        # vector for the key (the new value was not semantically
        # indexed); use write_semantic to (re-)index.
        await self._inner.write(key, value, ttl_seconds=ttl_seconds)
        self._vectors.pop(key, None)

    async def delete(self, key: str) -> None:
        await self._inner.delete(key)
        self._vectors.pop(key, None)

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

    async def query_semantic(self, text: str, *, k: int = 5) -> list[SemanticHit]:
        if k <= 0 or not self._vectors:
            return []
        query = (await self._embedder.embed([text]))[0]
        hits: list[SemanticHit] = []
        for key in list(self._vectors):
            value = await self._inner.read(key)
            if value is None:
                # Expired or deleted out from under the index; drop it.
                self._vectors.pop(key, None)
                continue
            hits.append(SemanticHit(key=key, score=_cosine(query, self._vectors[key]), value=value))
        # Descending similarity; ties broken by key for determinism.
        hits.sort(key=lambda h: (-h.score, h.key))
        return hits[:k]
