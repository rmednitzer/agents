"""Tests for harness.drift (BL-062)."""

from __future__ import annotations

import pytest

from harness.drift import DriftMonitor, jensen_shannon_divergence


def test_identical_distributions_zero() -> None:
    d = {"pass": 8, "fail": 2}
    assert jensen_shannon_divergence(d, dict(d)) == pytest.approx(0.0, abs=1e-12)


def test_disjoint_supports_one() -> None:
    assert jensen_shannon_divergence({"a": 1}, {"b": 1}) == pytest.approx(1.0)


def test_symmetric_and_bounded() -> None:
    p = {"a": 3, "b": 1}
    q = {"a": 1, "b": 3}
    ab = jensen_shannon_divergence(p, q)
    ba = jensen_shannon_divergence(q, p)
    assert ab == pytest.approx(ba)
    assert 0.0 < ab < 1.0


def test_empty_input_returns_zero() -> None:
    assert jensen_shannon_divergence({}, {"a": 1}) == 0.0
    assert jensen_shannon_divergence({"a": 1}, {}) == 0.0


def test_counts_vs_probabilities_equivalent() -> None:
    counts = jensen_shannon_divergence({"a": 6, "b": 2}, {"a": 1, "b": 3})
    probs = jensen_shannon_divergence({"a": 0.75, "b": 0.25}, {"a": 0.25, "b": 0.75})
    assert counts == pytest.approx(probs)


def test_drift_monitor_no_reference_is_zero() -> None:
    m = DriftMonitor()
    m.record("p", "pass")
    assert m.drift("p") == 0.0


def test_drift_monitor_detects_shift() -> None:
    m = DriftMonitor()
    for _ in range(9):
        m.record("p", "pass")
    m.record("p", "fail")
    m.snapshot_reference("p")
    assert m.drift("p") == pytest.approx(0.0, abs=1e-12)
    # Population shifts hard toward failure.
    for _ in range(40):
        m.record("p", "fail")
    assert m.drift("p") > 0.2
    assert m.distribution("p")["fail"] > m.distribution("p")["pass"]
