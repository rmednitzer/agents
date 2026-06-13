"""Read-side freshness gating and refusal-as-data (BL-246).

Run provenance (ADR 0012) is write-side: a `RunRecord` attests what a run
did. There is no read-side freshness contract that stamps a value with an
as-of instant and forces the agent to treat a stale value as suspect
before relying on it, and BL-137's typed soft-reject is one path, not a
uniform "refusal is data" shape.

This module adds the two read-side pieces from the operator-gateway
pattern, both additive (ADR 0007):

- `Refusal`: a small typed record so a tool can return a refusal as
  *model-legible data* (`{reason, detail}`) rather than raising or
  returning prose the model misreads. A workload wraps it in its output
  model the same way the degraded disposition (ADR 0030) stays a workload
  reporting concern; the substrate ships the type.
- `require_fresh`: a `Predicate` factory (the `grounding_predicate`
  shape, ADR 0030) that gates a value by age. The workload supplies an
  `extract` returning the value's as-of instant; the predicate passes
  iff that instant is within `max_age_seconds` of the clock. Freshness is
  inherently time-dependent, so the clock is injected (default the wall
  clock) to keep the check deterministic under test.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from harness.contract import FunctionPredicate, Severity

__all__ = ["Refusal", "is_stale", "require_fresh"]


class Refusal(BaseModel):
    """A model-legible refusal (BL-246): why a request was declined.

    Returning this from a tool (typically inside the workload's own
    result envelope) makes a refusal data the model can reason about,
    rather than an exception that aborts or a string it misreads.
    Immutable.
    """

    model_config = ConfigDict(frozen=True)

    reason: str
    detail: str = ""


def is_stale(as_of: datetime, max_age_seconds: float, *, now: datetime) -> bool:
    """Pure: whether ``as_of`` is older than ``max_age_seconds`` at ``now``.

    Both instants must be timezone-aware (a naive one raises, so the
    subtraction never raises a naive-vs-aware ``TypeError``), and
    ``max_age_seconds`` must be finite and non-negative (the BL-159 /
    BL-231 class). A value stamped in the future is never stale.
    """
    for label, value in (("as_of", as_of), ("now", now)):
        if value.utcoffset() is None:
            raise ValueError(f"{label} must be timezone-aware (UTC), got naive {value.isoformat()}")
    if not math.isfinite(max_age_seconds) or max_age_seconds < 0:
        raise ValueError(
            f"max_age_seconds must be finite and non-negative, got {max_age_seconds!r}"
        )
    return (now - as_of).total_seconds() > max_age_seconds


def _utcnow() -> datetime:
    return datetime.now(UTC)


def require_fresh[StateT](
    extract: Callable[[StateT], datetime],
    max_age_seconds: float,
    *,
    clock: Callable[[], datetime] = _utcnow,
    name: str = "fresh",
    severity: Severity = Severity.SOFT,
) -> FunctionPredicate[StateT]:
    """Build a freshness postcondition over a state (BL-246).

    ``extract(state)`` returns the value's as-of instant (the workload
    knows where the timestamp lives in its model). The predicate passes
    iff that instant is within ``max_age_seconds`` of ``clock()`` (default
    the wall clock; inject a fixed clock for deterministic tests).
    Defaults to ``Severity.SOFT`` so a stale read marks the run degraded
    (ADR 0030) rather than halting it; pass ``Severity.HARD`` to make a
    stale read a terminal ``PostconditionViolation``. ``max_age_seconds``
    is validated finite / non-negative at build time (ADR 0007).
    """
    if not math.isfinite(max_age_seconds) or max_age_seconds < 0:
        raise ValueError(
            f"max_age_seconds must be finite and non-negative, got {max_age_seconds!r}"
        )

    def _fresh(state: StateT) -> bool:
        return not is_stale(extract(state), max_age_seconds, now=clock())

    return FunctionPredicate(name=name, severity=severity, fn=_fresh)
