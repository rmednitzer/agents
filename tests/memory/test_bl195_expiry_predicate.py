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

import pytest

from memory._expiry import is_expired, is_live


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


def test_inmemory_uses_inclusive_boundary_at_expiry_instant() -> None:
    """The InMemoryStore reference adapter must use the helper, so an
    entry at the exact expiry instant is still readable, listable,
    scannable, and not yet swept. Regression guard against a future
    drift back into a strict ``>`` boundary."""
    import asyncio

    from memory.inmemory import InMemoryStore
    from memory.types import Namespace

    async def run() -> None:
        store = InMemoryStore(Namespace(name="ns", workload="w"))
        # Write with a TTL we can pin in time below. Patch time.time
        # so we can land exactly on the expiry instant.
        import time as _time

        real_time = _time.time
        # Anchor TTL relative to ``now``: write at t=100, ttl=10, so
        # expires_at=110. Then read at t=110: must still be live.
        called = {"n": 0}
        sequence = [100.0, 110.0, 110.0, 110.0001]

        def fake_time() -> float:
            i = called["n"]
            called["n"] = min(i + 1, len(sequence) - 1)
            return sequence[i]

        _time.time = fake_time  # type: ignore[assignment]
        try:
            await store.write("k", b"v", ttl_seconds=10.0)  # uses t=100
            assert await store.read("k") == b"v"  # at t=110: inclusive
            assert "k" in (await store.list_keys())  # also at t=110
            # Past the boundary, expired.
            _time.time = lambda: 110.0001  # type: ignore[assignment]
            assert await store.read("k") is None
        finally:
            _time.time = real_time  # type: ignore[assignment]

    asyncio.run(run())
