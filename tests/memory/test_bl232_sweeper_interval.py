"""Twelfth-audit numeric-configuration validation: `BL-232`
(`TTLSweeper.interval_seconds`), ADR 0022.

`TTLSweeper.__init__` rejected a non-positive interval with
``interval_seconds <= 0``, but `NaN <= 0` and `+inf <= 0` are both
False, so a non-finite interval passed a guard that claims "must be
positive". A `NaN` interval is the dangerous case: the loop awaits
``asyncio.wait_for(self._stop.wait(), timeout=NaN)``, which raises
`TimeoutError` immediately, so the sweeper degrades into a no-delay
busy-loop that hammers the backend's ``sweep_expired`` as fast as the
event loop allows. The ``math.isfinite`` conjunct closes the hole, the
``MCPServerSpec.timeout_seconds`` twin (`tests/harness`) and the
`Namespace.retention_seconds` (BL-197) class on the config boundary.
"""

from __future__ import annotations

import pytest

from memory.inmemory import InMemoryStore
from memory.sweep import TTLSweeper
from memory.types import Namespace


def _store() -> InMemoryStore:
    return InMemoryStore(Namespace(name="n", workload="w"))


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_sweeper_rejects_non_finite_interval(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        TTLSweeper(_store(), interval_seconds=bad)


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_sweeper_still_rejects_non_positive_interval(bad: float) -> None:
    with pytest.raises(ValueError, match="interval_seconds"):
        TTLSweeper(_store(), interval_seconds=bad)


def test_sweeper_accepts_positive_finite_interval() -> None:
    sweeper = TTLSweeper(_store(), interval_seconds=60.0)
    assert sweeper._interval == 60.0


def test_negative_inf_interval_is_rejected() -> None:
    """``-inf`` is caught by either conjunct (not finite, and <= 0); pin
    it so the boundary table is complete."""
    with pytest.raises(ValueError, match="interval_seconds"):
        TTLSweeper(_store(), interval_seconds=float("-inf"))
