"""Ranking metrics for dispatch evaluation (BL-130).

Pure functions over a ranked list of predicted skill names against a
single expected name. ``rank`` is 1-based; absence is rank 0 (no
credit). These are the standard retrieval metrics so a routing
regression is a number that moves, not a silent behaviour change
(LIMITATIONS L6).
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["hit_rank", "mean_reciprocal_rank", "precision_at_1", "reciprocal_rank"]


def hit_rank(predicted: Sequence[str], expected: str) -> int:
    """1-based rank of ``expected`` in ``predicted``; 0 if absent."""
    for i, name in enumerate(predicted):
        if name == expected:
            return i + 1
    return 0


def precision_at_1(ranks: Sequence[int]) -> float:
    """Fraction of cases whose top prediction was correct (rank == 1)."""
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if r == 1) / len(ranks)


def reciprocal_rank(rank: int) -> float:
    """1/rank, or 0.0 when the expected skill was not predicted."""
    return 1.0 / rank if rank > 0 else 0.0


def mean_reciprocal_rank(ranks: Sequence[int]) -> float:
    """Mean of the per-case reciprocal ranks."""
    if not ranks:
        return 0.0
    return sum(reciprocal_rank(r) for r in ranks) / len(ranks)
