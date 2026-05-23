"""Sixth-audit evaluation fix: regression test for `BL-206` (ADR 0015).

`BL-206` (input-validation mislabelled as `output_invalid`):
`evaluate_trajectory` wrapped `input_model.model_validate(...)` in the
same try as `run_under_contract` and mapped `pydantic.ValidationError`
to ``output_invalid``. A malformed `TrajectoryCase.input_payload`
therefore scored as a contract output failure (could even pass when
the author expected ``output_invalid``), silently green-lighting a
case that never reached the contract. The fix moves the validation
above the try so a fixture error raises at the fixture-error layer
rather than being misclassified.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from evaluation.harness import TrajectoryCase, evaluate_trajectory
from harness.contract import Contract, Severity, predicate


class _In(BaseModel):
    required: str


class _Out(BaseModel):
    text: str


class _StubRuntime:
    name = "stub"

    async def run(self, prompt: str, **_: Any) -> Any:
        return _Out(text="ok")

    def stream(self, prompt: str, **_: Any) -> AsyncIterator[Any]:
        raise NotImplementedError


def _contract() -> Contract[_In, _Out]:
    @predicate(name="x", severity=Severity.HARD)
    def _ok(_: Any) -> bool:
        return True

    return Contract[_In, _Out](
        name="audit6.bl206", version="1", preconditions=[_ok], postconditions=[_ok]
    )


@pytest.mark.asyncio
async def test_malformed_input_payload_raises_validation_error_not_mislabel() -> None:
    """A `TrajectoryCase` with a payload that violates the input model
    raises `ValidationError` from the evaluation harness rather than
    being scored as ``output_invalid``. Pre-`BL-206` the case scored
    as a contract regression (`passed=True` if the author wrote
    ``expected="output_invalid"``)."""
    case = TrajectoryCase(
        name="malformed",
        input_payload={"wrong_field": "x"},  # _In requires `required`
        expected="output_invalid",
    )
    with pytest.raises(ValidationError):
        await evaluate_trajectory(
            runtime=_StubRuntime(),
            contract=_contract(),
            input_model=_In,
            output_model=_Out,
            cases=[case],
        )


@pytest.mark.asyncio
async def test_well_formed_input_still_completes() -> None:
    """Backward compatibility: a well-formed payload completes
    normally."""
    case = TrajectoryCase(
        name="ok",
        input_payload={"required": "y"},
        expected="completed",
    )
    report = await evaluate_trajectory(
        runtime=_StubRuntime(),
        contract=_contract(),
        input_model=_In,
        output_model=_Out,
        cases=[case],
    )
    assert report.n == 1
    assert report.results[0].actual == "completed"
    assert report.results[0].passed is True
