"""Evaluation harness: measured dispatch + contract-trajectory quality.

The CI gates (lint, types, coverage) verify code shape, not agent
behaviour, so routing and contract-outcome quality can regress
silently (LIMITATIONS L6, S1 "measure against clear success criteria").
This runs a dispatcher over a golden set and reports P@1 / MRR, and
runs contracts over a trajectory fixture and reports the
expected-vs-actual terminal outcome. Both are deterministic and
network-free with a keyword dispatcher / stub runtime, so they are
CI-gateable (scripts/eval.py).
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evaluation.dataset import DispatchGoldenSet, TrajectoryCase, TrajectoryOutcome
from evaluation.metrics import hit_rank, mean_reciprocal_rank, precision_at_1
from harness.contract import Contract
from harness.enforcement import run_under_contract
from harness.errors import (
    ApprovalDenied,
    BudgetExceeded,
    GovernanceViolation,
    InvariantViolation,
    PostconditionViolation,
    PreconditionViolation,
)
from harness.interruption import ResumableState
from harness.runtime import Runtime
from skills.dispatcher import Dispatcher

__all__ = [
    "CaseResult",
    "DispatchReport",
    "TrajectoryReport",
    "TrajectoryResult",
    "evaluate_dispatch",
    "evaluate_trajectory",
]


class CaseResult(BaseModel):
    """One dispatch case outcome."""

    model_config = ConfigDict(frozen=True)

    query: str
    expected: str
    predicted: tuple[str, ...]
    rank: int  # 1-based; 0 == expected not in the predicted list


class DispatchReport(BaseModel):
    """Aggregate dispatch quality over a golden set."""

    model_config = ConfigDict(frozen=True)

    name: str
    n: int
    precision_at_1: float
    mrr: float
    results: tuple[CaseResult, ...] = Field(default_factory=tuple)

    def meets(self, *, min_p_at_1: float, min_mrr: float) -> bool:
        """True iff both metrics clear their thresholds (CI gate)."""
        return self.precision_at_1 >= min_p_at_1 and self.mrr >= min_mrr


async def evaluate_dispatch(
    dispatcher: Dispatcher,
    golden: DispatchGoldenSet,
    *,
    limit: int = 5,
) -> DispatchReport:
    """Run ``dispatcher`` over every case; compute P@1 and MRR.

    ``limit`` bounds how deep a correct-but-not-top prediction still
    earns reciprocal-rank credit.
    """
    results: list[CaseResult] = []
    ranks: list[int] = []
    for case in golden.cases:
        matches = await dispatcher.dispatch(case.query, limit=limit)
        predicted = tuple(m.skill_name for m in matches)
        rank = hit_rank(predicted, case.expected)
        ranks.append(rank)
        results.append(
            CaseResult(
                query=case.query,
                expected=case.expected,
                predicted=predicted,
                rank=rank,
            )
        )
    return DispatchReport(
        name=golden.name,
        n=len(results),
        precision_at_1=precision_at_1(ranks),
        mrr=mean_reciprocal_rank(ranks),
        results=tuple(results),
    )


class TrajectoryResult(BaseModel):
    """One trajectory case: expected vs actual terminal outcome."""

    model_config = ConfigDict(frozen=True)

    name: str
    expected: TrajectoryOutcome
    actual: TrajectoryOutcome
    passed: bool


class TrajectoryReport(BaseModel):
    """Aggregate contract-outcome accuracy over a trajectory fixture."""

    model_config = ConfigDict(frozen=True)

    n: int
    accuracy: float
    results: tuple[TrajectoryResult, ...] = Field(default_factory=tuple)


_EXC_LABEL: tuple[tuple[type[Exception], TrajectoryOutcome], ...] = (
    (PreconditionViolation, "precondition"),
    (InvariantViolation, "invariant"),
    (PostconditionViolation, "postcondition"),
    (GovernanceViolation, "governance"),
    (BudgetExceeded, "budget"),
    # run_under_contract also raises ApprovalDenied when a required
    # approval is rejected; without this it fell through and re-raised,
    # aborting the whole evaluation instead of scoring the case.
    (ApprovalDenied, "approval_denied"),
    # A runtime result that fails to parse into the output model raises
    # pydantic ValidationError out of run_under_contract; map it so the
    # case is scored "output_invalid" (matching RunRecord) instead of
    # aborting the whole evaluation. Listed last: it is the broadest
    # type here, and the harness exceptions above are not subclasses of
    # it, so ordering does not mis-route them.
    (ValidationError, "output_invalid"),
)


async def evaluate_trajectory[InputT: BaseModel, OutputT: BaseModel](
    runtime: Runtime,
    contract: Contract[InputT, OutputT],
    input_model: type[InputT],
    output_model: type[OutputT],
    cases: Sequence[TrajectoryCase],
) -> TrajectoryReport:
    """Run ``contract`` over each case; classify the terminal outcome.

    The outcome is "completed" on a clean return, otherwise the label
    of the hard violation / budget exception that terminated the run.
    Deterministic with a stub ``runtime``.
    """
    results: list[TrajectoryResult] = []
    for case in cases:
        actual: TrajectoryOutcome = "completed"
        # Input validation runs OUTSIDE the contract try/except (`BL-206`):
        # a malformed `input_payload` is a test-fixture error, not a
        # contract output failure; mapping `ValidationError` to
        # ``output_invalid`` inside the same try labelled fixture errors
        # as contract regressions and would silently green-light a case
        # that never reached the contract.
        validated_input = input_model.model_validate(case.input_payload)
        try:
            outcome = await run_under_contract(
                runtime=runtime,
                contract=contract,
                input=validated_input,
                output_model=output_model,
            )
            # run_under_contract returns a ResumableState (no exception)
            # when an approval-gated run pauses; that is NOT a terminal
            # success, so it must not be scored as "completed".
            if isinstance(outcome, ResumableState):
                actual = "paused"
        except Exception as exc:
            actual = next(
                (label for cls, label in _EXC_LABEL if isinstance(exc, cls)),
                "completed",
            )
            if actual == "completed":
                raise
        results.append(
            TrajectoryResult(
                name=case.name,
                expected=case.expected,
                actual=actual,
                passed=actual == case.expected,
            )
        )
    passed = sum(1 for r in results if r.passed)
    return TrajectoryReport(
        n=len(results),
        accuracy=passed / len(results) if results else 0.0,
        results=tuple(results),
    )
