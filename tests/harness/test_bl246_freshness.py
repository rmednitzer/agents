"""BL-246: read-side freshness gating and refusal-as-data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ValidationError

from harness.contract import Severity
from harness.freshness import Refusal, is_stale, require_fresh

_T0 = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)


class _Doc(BaseModel):
    as_of: datetime


# --- Refusal ------------------------------------------------------------


def test_refusal_is_typed_and_frozen() -> None:
    r = Refusal(reason="stale", detail="data older than 1h")
    assert r.reason == "stale"
    assert r.detail == "data older than 1h"
    with pytest.raises(ValidationError):
        r.reason = "changed"  # type: ignore[misc]


def test_refusal_detail_defaults_empty() -> None:
    assert Refusal(reason="withdrawn").detail == ""


# --- is_stale -----------------------------------------------------------


def test_is_stale_within_window_is_fresh() -> None:
    assert is_stale(_T0, 3600, now=_T0 + timedelta(seconds=1800)) is False


def test_is_stale_beyond_window() -> None:
    assert is_stale(_T0, 3600, now=_T0 + timedelta(seconds=3601)) is True


def test_is_stale_future_value_is_never_stale() -> None:
    assert is_stale(_T0 + timedelta(seconds=100), 10, now=_T0) is False


def test_is_stale_rejects_naive() -> None:
    naive = datetime(2026, 6, 13, 12, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        is_stale(naive, 60, now=_T0)
    with pytest.raises(ValueError, match="timezone-aware"):
        is_stale(_T0, 60, now=naive)


def test_is_stale_rejects_bad_max_age() -> None:
    for bad in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="max_age_seconds"):
            is_stale(_T0, bad, now=_T0)


# --- require_fresh ------------------------------------------------------


def _extract(d: _Doc) -> datetime:
    return d.as_of


def test_require_fresh_defaults_soft() -> None:
    pred = require_fresh(_extract, 3600)
    assert pred.severity is Severity.SOFT
    assert pred.name == "fresh"


def test_require_fresh_passes_when_fresh() -> None:
    clock = lambda: _T0 + timedelta(seconds=60)  # noqa: E731
    pred = require_fresh(_extract, 3600, clock=clock)
    assert pred(_Doc(as_of=_T0)) is True


def test_require_fresh_fails_when_stale() -> None:
    clock = lambda: _T0 + timedelta(seconds=7200)  # noqa: E731
    pred = require_fresh(_extract, 3600, clock=clock)
    assert pred(_Doc(as_of=_T0)) is False


def test_require_fresh_hard_and_custom_name() -> None:
    pred = require_fresh(_extract, 60, name="recent", severity=Severity.HARD)
    assert pred.severity is Severity.HARD
    assert pred.name == "recent"


def test_require_fresh_uses_wall_clock_by_default() -> None:
    # No injected clock: a just-now timestamp is fresh against the wall clock.
    pred = require_fresh(_extract, 3600)
    assert pred(_Doc(as_of=datetime.now(UTC))) is True


def test_require_fresh_validates_max_age_at_build() -> None:
    for bad in (-5.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="max_age_seconds"):
            require_fresh(_extract, bad)
