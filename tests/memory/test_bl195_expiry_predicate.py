"""Tests for memory._expiry: the consolidated expiry-boundary predicate
(BL-195, runbook 7.4 candidate 1).

The fault-class history (`BL-157` / `BL-168` / `BL-177` / `BL-188` /
`BL-180`) fixed the same invariant pointwise five times across the
adapters. This consolidation introduces a single ``is_live`` /
``is_expired`` helper in ``memory/_expiry.py`` so the Python-side
predicate has one definition; the SQL / DynamoDB-DSL forms are
documented in the helper's module docstring as equivalents. These
tests pin the invariant.
"""

from __future__ import annotations

import math
import time

import pytest

from memory._expiry import is_expired, is_live
from memory.inmemory import InMemoryStore
from memory.types import Namespace


@pytest.mark.parametrize(
    ("now", "expires_at", "expected_live"),
    [
        # No expiry: always live.
        (0.0, None, True),
        (1e9, None, True),
        # Live well before expiry.
        (10.0, 20.0, True),
        # Live at the exact boundary instant: inclusive (the BL-157 /
        # BL-168 / BL-177 / BL-188 fault class).
        (20.0, 20.0, True),
        # Expired immediately past the boundary.
        (20.0001, 20.0, False),
        # Far past expiry.
        (1e9, 20.0, False),
        # Expires_at == 0.0 (a TTL that elapsed by the epoch). Past
        # the boundary, expired; at zero, still live.
        (0.0, 0.0, True),
        (0.001, 0.0, False),
    ],
)
def test_is_live_boundary(now: float, expires_at: float | None, expected_live: bool) -> None:
    assert is_live(now, expires_at) is expected_live
    assert is_expired(now, expires_at) is (not expected_live)


def test_is_live_and_is_expired_are_total_negations() -> None:
    """For every (now, expires_at), exactly one of is_live / is_expired
    holds. The invariant the adapters rely on."""
    for now in (0.0, 1.0, 100.0, 1e9):
        for exp in (None, 0.0, 1.0, 99.999, 100.0, 100.001):
            assert is_live(now, exp) != is_expired(now, exp)


def test_none_expiry_is_never_expired() -> None:
    """``expires_at=None`` means "no expiry, always live"; the
    namespace.retention_seconds=None contract relies on this."""
    for now in (-1e9, 0.0, 1.0, 1e9):
        assert is_live(now, None) is True
        assert is_expired(now, None) is False


def test_nan_expiry_preserves_prior_adapter_behaviour() -> None:
    """Anomalous ``expires_at = NaN`` (a TTL=NaN propagated through
    ``write``) is reported live, matching the pre-BL-195 adapter
    behaviour (`now > NaN` is False -> not expired). The longer-term
    fix is validating TTL as finite at the API boundary; this test
    pins the consolidation as strictly behaviour-preserving."""
    nan = math.nan
    for now in (0.0, 100.0, 1e9):
        assert is_expired(now, nan) is False
        assert is_live(now, nan) is True


@pytest.mark.asyncio
async def test_inmemory_inclusive_boundary_across_read_list_scan_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the exact expiry instant, the InMemoryStore reference adapter
    must report an entry live across every interface that touches the
    boundary: ``read`` / ``list_keys`` / ``scan`` / ``sweep_expired``.
    Regression guard against a future drift back into a strict ``>``
    boundary (the BL-188 fault class), and against per-interface
    drift (the BL-157 / BL-168 / BL-177 fault classes).
    """
    store = InMemoryStore(Namespace(name="ns", workload="w"))

    # Phase 1: write at t=100 with TTL=10 -> expires_at = 110.
    monkeypatch.setattr(time, "time", lambda: 100.0)
    await store.write("k", b"v", ttl_seconds=10.0)

    # Phase 2: at exactly t=110 (the boundary instant), the entry is
    # live on every path that consults the predicate.
    monkeypatch.setattr(time, "time", lambda: 110.0)
    assert await store.read("k") == b"v"
    assert "k" in await store.list_keys()
    _, page = await store.scan(count=10)
    assert "k" in page
    assert await store.sweep_expired() == 0  # not swept at boundary

    # Phase 3: at t=110.0001 (strictly past the boundary), expired on
    # every path.
    monkeypatch.setattr(time, "time", lambda: 110.0001)
    assert await store.read("k") is None
    # ``read`` lazy-deletes; rewrite to test list/scan/sweep at the
    # post-boundary instant directly.
    monkeypatch.setattr(time, "time", lambda: 100.0)
    await store.write("k", b"v", ttl_seconds=10.0)
    monkeypatch.setattr(time, "time", lambda: 110.0001)
    assert "k" not in await store.list_keys()
    _, page = await store.scan(count=10)
    assert "k" not in page
    # And sweep removes it.
    monkeypatch.setattr(time, "time", lambda: 100.0)
    await store.write("k", b"v", ttl_seconds=10.0)
    monkeypatch.setattr(time, "time", lambda: 110.0001)
    assert await store.sweep_expired() == 1
