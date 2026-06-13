"""DEGRADED disposition + grounding postconditions (BL-244, ADR 0030).

Two coupled increments:

- ``harness.grounding``: the deterministic anti-confabulation check
  (``ungrounded_citations`` and the SOFT ``grounding_predicate`` factory).
- ``RunRecord.degraded``: set by ``run_under_contract`` when a SOFT
  postcondition is violated on the final delivered leg, so a completed
  run can still be flagged partial without halting or rewriting output.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from harness.contract import Contract, Severity, predicate
from harness.enforcement import run_under_contract
from harness.errors import PostconditionViolation
from harness.grounding import grounding_predicate, ungrounded_citations
from harness.provenance import RunRecord, record_invariant_violations
from harness.recovery import RecoveryOutcome

CVE = r"CVE-\d{4}-\d{4,}"


class _In(BaseModel):
    query: str


class _Doc(BaseModel):
    answer: str
    evidence: str


def _extract(o: _Doc) -> tuple[str, str]:
    return o.answer, o.evidence


_GROUNDED = _Doc(answer="Affected by CVE-2025-12345.", evidence="scan: CVE-2025-12345 present")
_UNGROUNDED = _Doc(
    answer="Affected by CVE-2025-12345 and CVE-2099-00001.",
    evidence="scan: CVE-2025-12345 present",
)


class _DocRuntime:
    """Returns a configured _Doc per leg, clamping to the last."""

    name = "doc"

    def __init__(self, *docs: _Doc) -> None:
        self._docs = list(docs) or [_GROUNDED]
        self.runs = 0

    async def run(self, prompt: str, **kw: Any) -> Any:
        doc = self._docs[min(self.runs, len(self._docs) - 1)]
        self.runs += 1
        return doc

    def stream(self, prompt: str, **kw: Any) -> AsyncIterator[Any]:
        raise NotImplementedError


class _Directive:
    """A recovery handler returning one fixed RecoveryOutcome."""

    def __init__(self, outcome: RecoveryOutcome) -> None:
        self._outcome = outcome

    async def recover(self, *, predicate: str, stage: str, state: Any) -> RecoveryOutcome:
        return self._outcome


# --- ungrounded_citations: the pure anti-confabulation core -----------


def test_ungrounded_returns_missing_citation() -> None:
    missing = ungrounded_citations(
        "Affected by CVE-2025-12345 and CVE-2099-00001.",
        "only CVE-2025-12345 was in the scan",
        pattern=CVE,
    )
    assert missing == ["CVE-2099-00001"]


def test_ungrounded_empty_when_every_citation_present() -> None:
    assert ungrounded_citations("CVE-2025-12345", "ref CVE-2025-12345 here", pattern=CVE) == []


def test_ungrounded_empty_when_claim_has_no_citations() -> None:
    # A claim with no matches is vacuously grounded.
    assert ungrounded_citations("no identifiers here", "sources", pattern=CVE) == []


def test_ungrounded_dedup_in_first_appearance_order() -> None:
    claim = "CVE-2099-00002, CVE-2099-00001, CVE-2099-00002, CVE-2099-00001"
    assert ungrounded_citations(claim, "", pattern=CVE) == ["CVE-2099-00002", "CVE-2099-00001"]


def test_ungrounded_returns_only_missing_among_mixed() -> None:
    claim = "CVE-2025-12345 (real) and CVE-2099-00001 (fabricated)"
    assert ungrounded_citations(claim, "CVE-2025-12345", pattern=CVE) == ["CVE-2099-00001"]


# --- grounding_predicate: the SOFT Predicate factory ------------------


def test_grounding_predicate_defaults_to_soft_named() -> None:
    pred = grounding_predicate(_extract, pattern=CVE)
    assert pred.severity == Severity.SOFT
    assert pred.name == "grounded_citations"


def test_grounding_predicate_passes_when_grounded() -> None:
    pred = grounding_predicate(_extract, pattern=CVE)
    assert pred(_GROUNDED) is True


def test_grounding_predicate_fails_when_ungrounded() -> None:
    pred = grounding_predicate(_extract, pattern=CVE)
    assert pred(_UNGROUNDED) is False


def test_grounding_predicate_hard_override() -> None:
    pred = grounding_predicate(_extract, pattern=CVE, severity=Severity.HARD)
    assert pred.severity == Severity.HARD


def test_grounding_predicate_custom_name() -> None:
    pred = grounding_predicate(_extract, pattern=CVE, name="cve_grounding")
    assert pred.name == "cve_grounding"


# --- RunRecord.degraded through run_under_contract --------------------


def _grounding_contract(severity: Severity = Severity.SOFT) -> Contract[_In, _Doc]:
    return Contract(
        name="retr",
        version="1.0.0",
        postconditions=[grounding_predicate(_extract, pattern=CVE, severity=severity)],
    )


async def test_grounded_completion_is_not_degraded() -> None:
    records: list[RunRecord] = []
    out = await run_under_contract(
        _DocRuntime(_GROUNDED),
        _grounding_contract(),
        _In(query="q"),
        _Doc,
        record_sink=records.append,
    )
    assert isinstance(out, _Doc)
    assert records[0].outcome == "completed"
    assert records[0].degraded is False


async def test_ungrounded_soft_completion_is_degraded() -> None:
    # The headline scenario: a fabricated CVE trips a SOFT grounding
    # postcondition; the run still delivers its output but is flagged.
    records: list[RunRecord] = []
    out = await run_under_contract(
        _DocRuntime(_UNGROUNDED),
        _grounding_contract(),
        _In(query="q"),
        _Doc,
        record_sink=records.append,
    )
    assert out == _UNGROUNDED  # soft never halts; output is delivered
    assert records[0].outcome == "completed"
    assert records[0].degraded is True


async def test_hard_grounding_violation_is_postcondition_not_degraded() -> None:
    records: list[RunRecord] = []
    with pytest.raises(PostconditionViolation):
        await run_under_contract(
            _DocRuntime(_UNGROUNDED),
            _grounding_contract(Severity.HARD),
            _In(query="q"),
            _Doc,
            record_sink=records.append,
        )
    # A hard failure is its own terminal outcome; degraded is the
    # orthogonal axis on a COMPLETED run and stays False here.
    assert records[0].outcome == "postcondition"
    assert records[0].degraded is False


async def test_retry_that_recovers_clears_degraded() -> None:
    # First leg ungrounded -> retry -> clean second leg: the final
    # delivered leg has no soft violation, so degraded resets to False.
    handler = _Directive(RecoveryOutcome(action="retry", directive="retry"))
    rt = _DocRuntime(_UNGROUNDED, _GROUNDED)
    records: list[RunRecord] = []
    out = await run_under_contract(
        rt,
        _grounding_contract(),
        _In(query="q"),
        _Doc,
        recovery={"grounded_citations": handler},
        record_sink=records.append,
    )
    assert rt.runs == 2
    assert out == _GROUNDED
    assert records[0].outcome == "completed"
    assert records[0].degraded is False


async def test_retry_exhausted_stays_degraded() -> None:
    # Retry is allowed once; if the second leg still fails the soft
    # postcondition, the run soft-continues and stays degraded.
    handler = _Directive(RecoveryOutcome(action="retry", directive="retry"))
    rt = _DocRuntime(_UNGROUNDED)  # every leg ungrounded
    records: list[RunRecord] = []
    out = await run_under_contract(
        rt,
        _grounding_contract(),
        _In(query="q"),
        _Doc,
        recovery={"grounded_citations": handler},
        record_sink=records.append,
    )
    assert rt.runs == 2
    assert isinstance(out, _Doc)
    assert records[0].degraded is True


async def test_substitute_directive_marks_degraded() -> None:
    # Substitution is NOT re-validated against the postconditions, and a
    # soft violation did occur on the delivered leg, so the honest
    # disposition is degraded (ADR 0030).
    handler = _Directive(
        RecoveryOutcome(action="sub", directive="substitute", replacement=_GROUNDED)
    )
    records: list[RunRecord] = []
    out = await run_under_contract(
        _DocRuntime(_UNGROUNDED),
        _grounding_contract(),
        _In(query="q"),
        _Doc,
        recovery={"grounded_citations": handler},
        record_sink=records.append,
    )
    assert out == _GROUNDED  # the substituted output is delivered
    assert records[0].outcome == "completed"
    assert records[0].degraded is True


async def test_escalate_directive_is_postcondition_not_degraded() -> None:
    handler = _Directive(RecoveryOutcome(action="esc", directive="escalate"))
    records: list[RunRecord] = []
    with pytest.raises(PostconditionViolation):
        await run_under_contract(
            _DocRuntime(_UNGROUNDED),
            _grounding_contract(),
            _In(query="q"),
            _Doc,
            recovery={"grounded_citations": handler},
            record_sink=records.append,
        )
    assert records[0].outcome == "postcondition"
    assert records[0].degraded is False


async def test_passing_soft_postcondition_among_a_failing_one() -> None:
    # One soft postcondition passes, the grounding one fails: a single
    # soft violation on the delivered leg is enough to mark degraded.
    @predicate(name="always_ok", severity=Severity.SOFT)
    def _always_ok(o: _Doc) -> bool:
        return True

    contract: Contract[_In, _Doc] = Contract(
        name="retr",
        version="1.0.0",
        postconditions=[_always_ok, grounding_predicate(_extract, pattern=CVE)],
    )
    records: list[RunRecord] = []
    await run_under_contract(
        _DocRuntime(_UNGROUNDED),
        contract,
        _In(query="q"),
        _Doc,
        record_sink=records.append,
    )
    assert records[0].degraded is True


async def test_all_soft_postconditions_pass_is_not_degraded() -> None:
    @predicate(name="also_ok", severity=Severity.SOFT)
    def _also_ok(o: _Doc) -> bool:
        return True

    contract: Contract[_In, _Doc] = Contract(
        name="retr",
        version="1.0.0",
        postconditions=[_also_ok, grounding_predicate(_extract, pattern=CVE)],
    )
    records: list[RunRecord] = []
    await run_under_contract(
        _DocRuntime(_GROUNDED),
        contract,
        _In(query="q"),
        _Doc,
        record_sink=records.append,
    )
    assert records[0].degraded is False


async def test_degraded_path_completes_without_a_record_sink() -> None:
    # The degraded bookkeeping must not perturb the BL-185 no-sink noop:
    # a soft violation with no record_sink still returns the output.
    out = await run_under_contract(
        _DocRuntime(_UNGROUNDED),
        _grounding_contract(),
        _In(query="q"),
        _Doc,
    )
    assert out == _UNGROUNDED


# --- record_invariant_violations: degraded-implies-completed ----------
# The structural half of the field's invariant is contract-independent,
# so the shared gate (verify_run_record + scripts/check_run_records.py)
# rejects a malformed producer that stamps degraded on a non-completed
# terminal (PR #117 review).


def _base_record(**kw: object) -> RunRecord:
    base: dict[str, object] = {
        "run_id": "trace-xyz",
        "workload": "retr",
        "contract_name": "retr",
        "contract_version": "1.0.0",
        "contract_digest": "0" * 64,
        "outcome": "completed",
        "started_at": "2026-06-13T00:00:00+00:00",
        "completed_at": "2026-06-13T00:00:01+00:00",
        "duration_ms": 1000.0,
    }
    base.update(kw)
    return RunRecord(**base)  # type: ignore[arg-type]


def test_degraded_on_completed_outcome_is_sound() -> None:
    assert record_invariant_violations(_base_record(degraded=True)) == []


def test_degraded_on_non_completed_outcome_is_flagged() -> None:
    errs = record_invariant_violations(_base_record(outcome="budget", degraded=True))
    assert any("degraded" in e and "budget" in e for e in errs)


def test_non_degraded_non_completed_outcome_is_sound() -> None:
    # The flag at its default never trips the invariant, on any outcome.
    assert record_invariant_violations(_base_record(outcome="budget", degraded=False)) == []
