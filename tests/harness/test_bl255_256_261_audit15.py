"""Fifteenth audit, harness findings: BL-255, BL-256, BL-261.

- BL-255: ``grounding_predicate`` short-circuits on the first ungrounded
  citation instead of building the full missing list, while still
  agreeing with ``ungrounded_citations``.
- BL-256: ``DriftMonitor.record`` rejects a non-finite / negative ``n``
  (the BL-159 / BL-205 / BL-221 / BL-231 / BL-232 non-finite class).
- BL-261: ``_with_evidence`` aborts the Tier 3 action and does NOT call
  ``after`` when the hook's ``before`` raises (the documented fail-safe
  contract; no completed action to record).
"""

from __future__ import annotations

from typing import Any

import pytest

from harness.authority import AuthorityTier
from harness.drift import DriftMonitor
from harness.grounding import grounding_predicate, ungrounded_citations
from harness.runtime import _GateResult, _with_evidence

_PAT = r"CVE-\d{4}-\d{4,}"


# --- BL-255: grounding short-circuit ----------------------------------


def test_grounding_predicate_flags_ungrounded_and_agrees_with_list() -> None:
    claim = "CVE-2024-0001 and CVE-2024-9999"
    sources = "advisory mentions CVE-2024-0001 only"
    pred = grounding_predicate(lambda _s: (claim, sources), pattern=_PAT)
    # CVE-2024-9999 is ungrounded, so the predicate fails (False) and
    # agrees with the diagnostic list builder.
    assert pred(None) is False
    assert ungrounded_citations(claim, sources, pattern=_PAT) == ["CVE-2024-9999"]


def test_grounding_predicate_passes_when_all_grounded() -> None:
    sources = "advisory mentions CVE-2024-0001 only"
    pred = grounding_predicate(lambda _s: ("CVE-2024-0001", sources), pattern=_PAT)
    assert pred(None) is True


def test_grounding_predicate_vacuous_on_no_matches() -> None:
    pred = grounding_predicate(lambda _s: ("no citations here", ""), pattern=_PAT)
    assert pred(None) is True


# --- BL-256: DriftMonitor non-finite n --------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1.0])
def test_drift_record_rejects_nonfinite_or_negative_n(bad: float) -> None:
    m = DriftMonitor()
    with pytest.raises(ValueError, match="finite and non-negative"):
        m.record("pred", "pass", bad)


def test_drift_record_accepts_finite_nonnegative_n() -> None:
    m = DriftMonitor()
    m.record("pred", "pass", 2.0)
    m.record("pred", "fail", 0.0)  # zero is a no-op, allowed
    assert m.distribution("pred") == {"pass": 1.0, "fail": 0.0}


# --- BL-261: evidence before() failure aborts and skips after() -------


class _FailingBeforeHook:
    def __init__(self) -> None:
        self.after_called = False

    async def before(self, context: Any) -> Any:
        raise RuntimeError("snapshot failed")

    async def after(self, token: Any, *, error: BaseException | None = None) -> None:
        self.after_called = True


async def test_with_evidence_before_failure_aborts_action_and_skips_after() -> None:
    hook = _FailingBeforeHook()
    ran = False

    async def _run() -> str:
        nonlocal ran
        ran = True
        return "did run"

    gate = _GateResult(soft=None, tier=AuthorityTier.IRREVERSIBLE, rollback_plan=None)
    with pytest.raises(RuntimeError, match="snapshot failed"):
        await _with_evidence(
            hook, gate, tool="delete_data", arguments={}, tool_call_id=None, run=_run
        )
    assert ran is False  # the irreversible action did not execute
    assert hook.after_called is False  # after() not called when before() failed
