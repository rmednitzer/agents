"""BL-251: approval-context payload (tier + rollback plan) on the interruption.

The data-carrying half of BL-251 (ADR 0031): the blast-radius
``AuthorityTier`` (already on ``GuardResponse`` from BL-242) and a
workload-supplied rollback plan now travel onto the human-facing
``ApprovalInterruption``, symmetrically across the replay and deferred
resume paths. The behavioural half (evidence-capture hook, two-step
parameter restatement) is split forward to BL-252.

Deterministic and network-free (FunctionModel, ADR 0001).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from harness.authority import (
    AuthorityTier,
    MappingRollbackPlanner,
    MappingTierClassifier,
    RollbackPlanner,
)
from harness.contract import Contract
from harness.enforcement import run_under_contract
from harness.guard import GuardDecision, HarnessToolGuard, ToolGuard
from harness.interruption import ApprovalInterruption, ResumableState

_PLAN = "scale prod back to the prior revision"


def _planner() -> MappingRollbackPlanner:
    return MappingRollbackPlanner({"deploy": _PLAN})


# --- MappingRollbackPlanner ---------------------------------------------


def test_mapping_planner_maps_known_tool() -> None:
    assert _planner().plan("deploy", {"env": "prod"}) == _PLAN


def test_mapping_planner_unlisted_tool_is_none() -> None:
    assert _planner().plan("read_metrics", {}) is None


def test_mapping_planner_ignores_arguments() -> None:
    p = _planner()
    assert p.plan("deploy", {}) == p.plan("deploy", {"env": "staging"}) == _PLAN


def test_mapping_planner_satisfies_protocol() -> None:
    assert isinstance(_planner(), RollbackPlanner)


# --- guard: rollback_plan on the GuardResponse --------------------------


def _gated_guard(**kwargs: Any) -> HarnessToolGuard:
    # deploy is STATEFUL (>= the default approval_tier), so it requires
    # approval; everything else is OBSERVE and approves.
    contract: Contract[None, None] = Contract(name="c", version="0.1.0")
    return HarnessToolGuard(
        contract,
        tier_classifier=MappingTierClassifier(
            {"deploy": AuthorityTier.STATEFUL}, default=AuthorityTier.OBSERVE
        ),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_guard_attaches_rollback_plan_on_approval() -> None:
    resp = await _gated_guard(rollback_planner=_planner()).check("deploy", {"env": "prod"})
    assert resp.decision == GuardDecision.REQUIRE_APPROVAL
    assert resp.tier is AuthorityTier.STATEFUL
    assert resp.rollback_plan == _PLAN


@pytest.mark.asyncio
async def test_guard_rollback_plan_none_without_planner() -> None:
    resp = await _gated_guard().check("deploy", {"env": "prod"})
    assert resp.decision == GuardDecision.REQUIRE_APPROVAL
    assert resp.rollback_plan is None


@pytest.mark.asyncio
async def test_guard_planner_not_consulted_on_approve() -> None:
    # read_metrics approves (OBSERVE); the planner never runs, so even a
    # tool the planner could describe carries no plan on an APPROVE.
    planner = MappingRollbackPlanner({"read_metrics": "should not appear"})
    resp = await _gated_guard(rollback_planner=planner).check("read_metrics", {})
    assert resp.decision == GuardDecision.APPROVE
    assert resp.rollback_plan is None


@pytest.mark.asyncio
async def test_guard_rollback_plan_none_when_planner_has_no_entry() -> None:
    # A planner that returns None for the gated tool leaves the field None.
    resp = await _gated_guard(rollback_planner=MappingRollbackPlanner({})).check("deploy", {})
    assert resp.decision == GuardDecision.REQUIRE_APPROVAL
    assert resp.rollback_plan is None


# --- ApprovalInterruption serialization ---------------------------------


def test_interruption_round_trips_tier_and_plan_through_json() -> None:
    ai = ApprovalInterruption(
        id="x",
        created_at="2026-06-13T00:00:00+00:00",  # type: ignore[arg-type]
        tool="deploy",
        arguments={"env": "prod"},
        tier=AuthorityTier.IRREVERSIBLE,
        rollback_plan=_PLAN,
    )
    dumped = ai.model_dump(mode="json")
    assert dumped["tier"] == 3  # IntEnum serialises to its int value
    assert dumped["rollback_plan"] == _PLAN
    reloaded = ApprovalInterruption.model_validate_json(json.dumps(dumped))
    assert reloaded.tier is AuthorityTier.IRREVERSIBLE
    assert reloaded.rollback_plan == _PLAN


def test_interruption_defaults_are_none() -> None:
    ai = ApprovalInterruption(
        id="x",
        created_at="2026-06-13T00:00:00+00:00",  # type: ignore[arg-type]
        tool="t",
    )
    assert ai.tier is None
    assert ai.rollback_plan is None


# --- end-to-end through the runtime: both resume paths -------------------


def _two_step_model() -> FunctionModel:
    """Propose record(), then deploy(), then finish."""

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        done = sum(
            1
            for m in messages
            for p in getattr(m, "parts", [])
            if getattr(p, "part_kind", "") in ("tool-return", "retry-prompt")
        )
        if done == 0:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="record", args={"note": "s1"}, tool_call_id="r1")]
            )
        if done == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="deploy", args={"env": "prod"}, tool_call_id="d1")]
            )
        return ModelResponse(parts=[TextPart(content="final")])

    return FunctionModel(fn)


def _tools() -> list[Any]:
    async def record(note: str) -> str:
        return "recorded"

    async def deploy(env: str) -> str:
        return f"deployed {env}"

    return [record, deploy]


@pytest.mark.asyncio
async def test_replay_pause_carries_tier_and_rollback_plan() -> None:
    from harness.runtime import PydanticAIRuntime

    rt = PydanticAIRuntime(_two_step_model())
    state = await rt.run("go", tools=_tools(), guard=_gated_guard(rollback_planner=_planner()))
    assert isinstance(state, ResumableState)
    [pending] = state.pending_approvals
    assert pending.tool == "deploy"
    assert pending.tier is AuthorityTier.STATEFUL
    assert pending.rollback_plan == _PLAN


@pytest.mark.asyncio
async def test_deferred_pause_carries_tier_and_rollback_plan() -> None:
    from harness.runtime import PydanticAIRuntime

    rt = PydanticAIRuntime(_two_step_model(), approval_mode="deferred")
    state = await rt.run("go", tools=_tools(), guard=_gated_guard(rollback_planner=_planner()))
    assert isinstance(state, ResumableState)
    [pending] = state.pending_approvals
    assert pending.tool == "deploy"
    assert pending.id == "d1"
    # Symmetric with the replay path: the gate recorded the context by
    # tool_call_id and the deferred pause read it back.
    assert pending.tier is AuthorityTier.STATEFUL
    assert pending.rollback_plan == _PLAN


@pytest.mark.asyncio
async def test_pause_without_planner_or_classifier_is_l1() -> None:
    # A plain approval_required gate (no classifier, no planner) leaves
    # both fields None: the L1 interruption shape is unchanged.
    from harness.runtime import PydanticAIRuntime

    contract: Contract[None, None] = Contract(
        name="c", version="0.1.0", approval_required=["deploy"]
    )
    guard = HarnessToolGuard(contract)
    rt = PydanticAIRuntime(_two_step_model())
    state = await rt.run("go", tools=_tools(), guard=guard)
    assert isinstance(state, ResumableState)
    [pending] = state.pending_approvals
    assert pending.tier is None
    assert pending.rollback_plan is None


# --- enforcement threading ----------------------------------------------


class _In(BaseModel):
    query: str


class _Out(BaseModel):
    text: str


class _RecordingRuntime:
    """Captures the guard the harness constructed."""

    name = "recording"

    def __init__(self) -> None:
        self.received_guard: ToolGuard | None = None

    async def run(self, prompt: str, *, guard: ToolGuard | None = None, **kw: Any) -> Any:
        self.received_guard = guard
        return _Out(text="ok")

    def stream(
        self, prompt: str, *, guard: ToolGuard | None = None, **kw: Any
    ) -> AsyncIterator[Any]:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_run_under_contract_threads_planner_into_default_guard() -> None:
    contract: Contract[_In, _Out] = Contract(
        name="c", version="0.1.0", approval_required=["deploy"]
    )
    runtime = _RecordingRuntime()
    await run_under_contract(
        runtime,
        contract,
        _In(query="hi"),
        _Out,
        rollback_planner=_planner(),
    )
    assert runtime.received_guard is not None
    resp = await runtime.received_guard.check("deploy", {"env": "prod"})
    assert resp.decision == GuardDecision.REQUIRE_APPROVAL
    assert resp.rollback_plan == _PLAN


@pytest.mark.asyncio
async def test_planner_alone_does_not_build_a_guard() -> None:
    # A planner only annotates an approval some other rule requires, so it
    # must not, by itself, trigger guard construction.
    contract: Contract[_In, _Out] = Contract(name="c", version="0.1.0")
    runtime = _RecordingRuntime()
    await run_under_contract(
        runtime,
        contract,
        _In(query="hi"),
        _Out,
        rollback_planner=_planner(),
    )
    assert runtime.received_guard is None
