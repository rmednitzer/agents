"""Golden datasets for evaluation (BL-130).

A dispatch golden set is a list of ``(query, expected_skill)`` cases,
loadable from JSON so the fixture is data, not code, and reviewable in
a diff. A trajectory golden set pairs an input payload with the
contract terminal outcome it must produce; contracts are code, so a
trajectory set is constructed programmatically (see
``evaluation.harness.evaluate_trajectory``).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DispatchCase",
    "DispatchGoldenSet",
    "TrajectoryCase",
    "TrajectoryOutcome",
    "load_dispatch_golden",
]

# The terminal outcome a contract run can reach. "completed" is a clean
# success; "paused" is an approval interruption (a ResumableState, not
# a terminal success); "approval_denied" is a rejected required
# approval; "output_invalid" is a runtime result that fails to parse
# into the output model; the others name which obligation failed hard.
# Kept in lockstep with harness.provenance.RunOutcome.
TrajectoryOutcome = Literal[
    "completed",
    "paused",
    "approval_denied",
    "output_invalid",
    "precondition",
    "invariant",
    "postcondition",
    "governance",
    "budget",
]


class DispatchCase(BaseModel):
    """One routing expectation: ``query`` should route to ``expected``."""

    model_config = ConfigDict(frozen=True)

    query: str
    expected: str
    note: str = ""


class DispatchGoldenSet(BaseModel):
    """A named set of dispatch cases."""

    model_config = ConfigDict(frozen=True)

    name: str
    cases: tuple[DispatchCase, ...] = Field(default_factory=tuple)


class TrajectoryCase(BaseModel):
    """An input payload and the contract terminal outcome it must reach."""

    model_config = ConfigDict(frozen=True)

    name: str
    input_payload: dict[str, object]
    expected: TrajectoryOutcome


def load_dispatch_golden(path: str | Path) -> DispatchGoldenSet:
    """Load a DispatchGoldenSet from a JSON file.

    Schema: ``{"name": str, "cases": [{"query", "expected", "note"?}]}``.
    Validation errors surface as pydantic ValidationError so a malformed
    fixture fails the eval at load, not mid-run.
    """
    # BL-218: pin UTF-8 explicitly (parity with the rest of the
    # explicit-encoding convention) so a non-default platform locale
    # cannot silently mis-decode a golden-set fixture carrying
    # non-ASCII query text.
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return DispatchGoldenSet.model_validate(data)


def dispatch_golden_from_cases(name: str, cases: Sequence[DispatchCase]) -> DispatchGoldenSet:
    """Build a DispatchGoldenSet programmatically (tests/helpers)."""
    return DispatchGoldenSet(name=name, cases=tuple(cases))
