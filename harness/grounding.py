"""Grounding postcondition reference for anti-confabulation (BL-244).

A retrieval agent's highest-value output check is grounding: every
citation-shaped token the model emits should appear verbatim in the
material it was given (the captured tool output, the retrieved sources).
The operator-gateway reliability runbook makes exactly this check
("every cited CVE-YYYY-N must appear in captured tool output") a
deterministic postcondition that relabels a run as degraded without
rewriting the model's content.

This module ships the deterministic core: ``ungrounded_citations`` (a
pure function) and ``grounding_predicate``, a SOFT ``Predicate`` factory.
A SOFT violation does not halt the run; it marks the delivered output
degraded (``RunRecord.degraded``, ADR 0030). The predicate is generic
over the output state via a caller-supplied ``extract`` that returns the
claim text and the grounding sources, since the substrate does not know
the output model's shape (the ``Embedder`` / ``TierClassifier`` injection
stance, ADR 0001).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from harness.contract import FunctionPredicate, Severity

__all__ = ["grounding_predicate", "ungrounded_citations"]


def _ungrounded(claim: str, sources: str, compiled: re.Pattern[str]) -> list[str]:
    """Distinct matches of ``compiled`` in ``claim`` absent from ``sources``.

    First-appearance order, deduplicated. A token is grounded iff it
    appears as a substring of ``sources``.
    """
    seen: set[str] = set()
    missing: list[str] = []
    for match in compiled.finditer(claim):
        token = match.group(0)
        if token in seen:
            continue
        seen.add(token)
        if token not in sources:
            missing.append(token)
    return missing


def ungrounded_citations(claim: str, sources: str, *, pattern: str) -> list[str]:
    """Return the citation tokens in ``claim`` not present in ``sources``.

    Every distinct match of the regular expression ``pattern`` in
    ``claim`` (e.g. ``r"CVE-\\d{4}-\\d{4,}"`` for CVE ids) must appear as
    a substring of ``sources`` (the captured tool output / retrieved
    material). Returns the ungrounded matches in first-appearance order,
    deduplicated; an empty list means every cited token is grounded (and
    a claim with no matches is vacuously grounded). Pure and
    deterministic: the anti-confabulation check, not a rewrite.
    """
    return _ungrounded(claim, sources, re.compile(pattern))


def grounding_predicate[StateT](
    extract: Callable[[StateT], tuple[str, str]],
    *,
    pattern: str,
    name: str = "grounded_citations",
    severity: Severity = Severity.SOFT,
) -> FunctionPredicate[StateT]:
    """Build a grounding postcondition over an output state (BL-244).

    ``extract(state)`` returns ``(claim, sources)``: the model's claim
    text and the grounding material it must be supported by (the workload
    knows where each lives in its output model). The predicate passes iff
    every citation matching ``pattern`` in the claim appears in the
    sources (``ungrounded_citations`` is empty). Defaults to
    ``Severity.SOFT`` so a violation does not halt the run; it marks the
    delivered output degraded (``RunRecord.degraded``, ADR 0030),
    relabelling without rewriting the model's content. Pass
    ``Severity.HARD`` to make ungrounded output a terminal
    ``PostconditionViolation`` instead.
    """
    compiled = re.compile(pattern)

    def _grounded(state: StateT) -> bool:
        claim, sources = extract(state)
        return not _ungrounded(claim, sources, compiled)

    return FunctionPredicate(name=name, severity=severity, fn=_grounded)
