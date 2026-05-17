"""EmbeddingProvider Protocol for the embedding dispatcher (BL-051).

The framework does not bind to any embedding vendor (mirrors ADR 0001's
runtime stance and ADR 0006's "the framework does not pick a model").
A caller supplies an EmbeddingProvider; the EmbeddingDispatcher only
needs vectors back. Any model-free deterministic provider works for
tests.
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

__all__ = ["EmbeddingProvider", "cosine_similarity"]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Maps texts to fixed-width float vectors.

    Implementations must return one vector per input text, in order,
    all of the same dimensionality. May call a remote model; that cost
    and any batching are the provider's concern, not the dispatcher's.
    """

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1].

    Returns 0.0 if either vector is zero-norm or carries a non-finite
    component (NaN or +/-inf, e.g. a buggy provider or adversarial skill
    text the provider maps to NaN). A non-finite score must never
    survive: ``min``/``max`` clamping treats NaN as the other operand,
    so an unguarded NaN would clamp to 1.0 and sort to the top of the
    dispatch ranking.
    """
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} != {len(b)}")
    dot = math.fsum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(math.fsum(x * x for x in a))
    nb = math.sqrt(math.fsum(y * y for y in b))
    if na == 0.0 or nb == 0.0 or not math.isfinite(na) or not math.isfinite(nb):
        return 0.0
    score = dot / (na * nb)
    return score if math.isfinite(score) else 0.0
