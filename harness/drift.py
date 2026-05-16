"""Distributional drift instrumentation (BL-062; ADR 0002, ADR 0007).

ADR 0002 deferred "JSD distributional drift instrumentation across
runs. Aggregated state distribution per predicate." A DriftMonitor
accumulates, per predicate, a categorical distribution of an
*aggregated state projection* the caller chooses (e.g. pass/fail, or a
bucketed numeric feature). Snapshot a reference distribution, keep
recording, and read back the Jensen-Shannon divergence between the
reference and the live distribution -- a bounded [0, 1] drift signal
suitable for alerting.

Pure stdlib; no numpy. JSD is symmetric and, with log base 2, bounded
in [0, 1], which makes thresholds portable across predicates.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping

__all__ = ["DriftMonitor", "jensen_shannon_divergence"]


def _normalize(counts: Mapping[str, float]) -> dict[str, float]:
    total = math.fsum(counts.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in counts.items()}


def jensen_shannon_divergence(
    p: Mapping[str, float],
    q: Mapping[str, float],
) -> float:
    """JSD between two categorical distributions, log base 2, in [0, 1].

    Inputs may be counts or probabilities (each is normalized). Disjoint
    supports give 1.0; identical distributions give 0.0. Empty input on
    either side returns 0.0 (no signal yet).
    """
    pn = _normalize(p)
    qn = _normalize(q)
    if not pn or not qn:
        return 0.0

    def _kl(a: Mapping[str, float], m: Mapping[str, float]) -> float:
        total = 0.0
        for k, av in a.items():
            if av > 0.0:
                total += av * math.log2(av / m[k])
        return total

    support = set(pn) | set(qn)
    m = {k: 0.5 * (pn.get(k, 0.0) + qn.get(k, 0.0)) for k in support}
    jsd = 0.5 * _kl(pn, m) + 0.5 * _kl(qn, m)
    # Clamp tiny negative/over-unity values from float error.
    return max(0.0, min(1.0, jsd))


class DriftMonitor:
    """Per-predicate categorical distributions + JSD vs a reference.

    Usage::

        m = DriftMonitor()
        for run in baseline:
            m.record("answer_grounded", "pass" if ok else "fail")
        m.snapshot_reference("answer_grounded")
        # ... later runs ...
        m.record("answer_grounded", "fail")
        drift = m.drift("answer_grounded")   # JSD in [0, 1]
    """

    def __init__(self) -> None:
        self._counts: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._reference: dict[str, dict[str, float]] = {}

    def record(self, predicate: str, category: str, n: float = 1.0) -> None:
        """Add ``n`` observations of ``category`` for ``predicate``."""
        self._counts[predicate][category] += n

    def distribution(self, predicate: str) -> dict[str, float]:
        """Current normalized distribution for ``predicate`` (may be empty)."""
        return _normalize(self._counts.get(predicate, {}))

    def snapshot_reference(self, predicate: str) -> None:
        """Freeze the current distribution as the drift baseline."""
        self._reference[predicate] = dict(self._counts.get(predicate, {}))

    def drift(self, predicate: str) -> float:
        """JSD between the reference and the live distribution.

        0.0 if no reference was snapshotted or there is no data yet.
        """
        ref = self._reference.get(predicate)
        if ref is None:
            return 0.0
        return jensen_shannon_divergence(ref, self._counts.get(predicate, {}))
