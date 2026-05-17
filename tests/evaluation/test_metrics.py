"""Tests for evaluation.metrics (BL-130)."""

from __future__ import annotations

import pytest

from evaluation.metrics import (
    hit_rank,
    mean_reciprocal_rank,
    precision_at_1,
    reciprocal_rank,
)


def test_hit_rank() -> None:
    assert hit_rank(["a", "b", "c"], "a") == 1
    assert hit_rank(["a", "b", "c"], "c") == 3
    assert hit_rank(["a", "b"], "z") == 0
    assert hit_rank([], "a") == 0


def test_reciprocal_rank() -> None:
    assert reciprocal_rank(1) == 1.0
    assert reciprocal_rank(2) == 0.5
    assert reciprocal_rank(0) == 0.0


def test_precision_at_1() -> None:
    assert precision_at_1([1, 1, 2, 0]) == pytest.approx(0.5)
    assert precision_at_1([1, 1]) == 1.0
    assert precision_at_1([]) == 0.0


def test_mean_reciprocal_rank() -> None:
    assert mean_reciprocal_rank([1, 2, 0]) == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert mean_reciprocal_rank([]) == 0.0
