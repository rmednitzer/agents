"""Tests for evaluation.harness + dataset (BL-130)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from evaluation.dataset import (
    DispatchCase,
    DispatchGoldenSet,
    TrajectoryCase,
    dispatch_golden_from_cases,
    load_dispatch_golden,
)
from evaluation.harness import evaluate_dispatch, evaluate_trajectory
from harness.contract import Contract, Severity, predicate
from skills.dispatchers.keyword import KeywordDispatcher
from skills.registry import SkillRegistry
from skills.types import Skill, SkillManifest

_REPO = Path(__file__).resolve().parents[2]


def _skill(name: str, desc: str, triggers: str) -> Skill:
    return Skill(
        manifest=SkillManifest(
            name=name, description=desc, metadata={"triggers": triggers}
        ),
        path=Path("/tmp/" + name),
    )


def _registry() -> SkillRegistry:
    r = SkillRegistry()
    r.add(_skill("alpha", "alpha skill about cats", "cat, feline"))
    r.add(_skill("beta", "beta skill about dogs", "dog, canine"))
    return r


@pytest.mark.asyncio
async def test_evaluate_dispatch_perfect() -> None:
    golden = dispatch_golden_from_cases(
        "g",
        [
            DispatchCase(query="my cat is hungry", expected="alpha"),
            DispatchCase(query="walk the dog", expected="beta"),
        ],
    )
    report = await evaluate_dispatch(KeywordDispatcher(_registry()), golden)
    assert report.n == 2
    assert report.precision_at_1 == 1.0
    assert report.mrr == 1.0
    assert report.meets(min_p_at_1=1.0, min_mrr=1.0)


@pytest.mark.asyncio
async def test_evaluate_dispatch_miss_lowers_metrics() -> None:
    golden = dispatch_golden_from_cases(
        "g", [DispatchCase(query="nothing relevant here", expected="alpha")]
    )
    report = await evaluate_dispatch(KeywordDispatcher(_registry()), golden)
    assert report.precision_at_1 == 0.0
    assert report.results[0].rank == 0
    assert not report.meets(min_p_at_1=0.5, min_mrr=0.5)


def test_load_dispatch_golden_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"name": "x", "cases": [{"query": "q", "expected": "e"}]}))
    g = load_dispatch_golden(p)
    assert isinstance(g, DispatchGoldenSet)
    assert g.cases[0].query == "q"


def test_load_dispatch_golden_rejects_malformed(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"name": "x", "cases": [{"query": "q"}]}))  # no expected
    with pytest.raises(ValidationError):
        load_dispatch_golden(p)


def test_in_tree_golden_is_valid_and_passes() -> None:
    """The shipped golden set must stay loadable and the deterministic
    dispatcher must keep P@1 == MRR == 1.0 (this is the CI gate)."""
    import asyncio

    golden = load_dispatch_golden(_REPO / "evaluation" / "data" / "skills_dispatch.json")
    registry = SkillRegistry.from_directory(_REPO / "skills")
    report = asyncio.run(evaluate_dispatch(KeywordDispatcher(registry), golden))
    assert report.meets(min_p_at_1=1.0, min_mrr=1.0)


# --- trajectory evaluation -------------------------------------------


class _In(BaseModel):
    ok: bool


class _Out(BaseModel):
    text: str


class _Stub:
    name = "stub"

    async def run(self, prompt: str, **kw: Any) -> Any:
        return _Out(text="done")

    def stream(self, prompt: str, **kw: Any) -> AsyncIterator[Any]:
        raise NotImplementedError


@predicate(name="must_be_ok", severity=Severity.HARD)
def _must_be_ok(i: _In) -> bool:
    return i.ok


@pytest.mark.asyncio
async def test_evaluate_trajectory_classifies_outcomes() -> None:
    contract: Contract[_In, _Out] = Contract(
        name="c", version="1", preconditions=[_must_be_ok]
    )
    cases = [
        TrajectoryCase(name="good", input_payload={"ok": True}, expected="completed"),
        TrajectoryCase(
            name="bad", input_payload={"ok": False}, expected="precondition"
        ),
        TrajectoryCase(
            name="mismatch", input_payload={"ok": False}, expected="completed"
        ),
    ]
    report = await evaluate_trajectory(_Stub(), contract, _In, _Out, cases)
    assert report.n == 3
    by_name = {r.name: r for r in report.results}
    assert by_name["good"].actual == "completed"
    assert by_name["good"].passed
    assert by_name["bad"].actual == "precondition"
    assert by_name["bad"].passed
    assert by_name["mismatch"].actual == "precondition"
    assert not by_name["mismatch"].passed
    assert report.accuracy == pytest.approx(2 / 3)
