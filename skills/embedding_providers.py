"""Concrete EmbeddingProvider implementations (BL-110).

``skills.embeddings`` defines the ``EmbeddingProvider`` Protocol but
shipped no concrete implementation, so ``EmbeddingDispatcher`` needed a
hand-rolled provider even for tests. ``HashingEmbeddingProvider`` is a
deterministic, dependency-free implementation (the hashing trick): it
makes the embedding dispatcher usable out of the box and gives CI a
network-free, reproducible vector backend.

A production, model-quality provider is intentionally NOT vendored: the
framework binds no embedding vendor (mirrors ADR 0001's runtime stance
and ADR 0006's "the framework does not pick a model"). A vendor-backed
provider is a few lines satisfying the same Protocol, importing its SDK
lazily, and lives with the workload that chooses the vendor.
"""

from __future__ import annotations

import hashlib
import math
import re

__all__ = ["HashingEmbeddingProvider"]

_TOKEN = re.compile(r"\w+")


class HashingEmbeddingProvider:
    """Deterministic feature-hashing text embeddings.

    Each text is lowercased and tokenised; every token is hashed to a
    bucket in ``[0, dim)`` with a sign hash (to reduce collision bias)
    and accumulated, then the vector is L2-normalised. Identical text
    always yields an identical vector (no network, no model, no state),
    and lexical overlap maps to cosine proximity, which is enough to
    exercise and smoke-test ``EmbeddingDispatcher`` deterministically.
    It is a baseline, not a semantic model: it captures token overlap,
    not meaning.

    Satisfies the ``skills.embeddings.EmbeddingProvider`` Protocol.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in _TOKEN.findall(text.lower()):
            h = hashlib.sha256(token.encode()).digest()
            bucket = int.from_bytes(h[:4], "big") % self._dim
            sign = 1.0 if h[4] & 1 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(math.fsum(x * x for x in vec))
        if norm == 0.0:
            return vec
        return [x / norm for x in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """One unit-norm vector per input text, in order, all width ``dim``."""
        return [self._vector(t) for t in texts]
