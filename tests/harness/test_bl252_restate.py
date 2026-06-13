"""BL-252: two-step parameter restatement for an irreversible action.

A Tier 3 (IRREVERSIBLE) approval is honoured only when the human
re-enters the arguments and they match the proposed call (ADR 0033),
composing with the BL-193 (tool, arguments) binding. A missing or
mismatched restatement re-pauses for a fresh decision, in both the
replay and deferred resume paths. Lower tiers keep their single-step
approval. Deterministic and network-free (FunctionModel, ADR 0001).

The evidence-capture hook (the other half of the original BL-252) is
split forward to BL-253.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from harness.authority import AuthorityTier, MappingTierClassifier
from harness.contract import Contract
from harness.guard import HarnessToolGuard
from harness.interruption import ApprovalInterruption, ResumableState
from harness.runtime import PydanticAIRuntime, _restate_satisfied

_ARGS = {"path": "/db"}


# --- _restate_satisfied unit -------------------------------------------


def _interruption(
    tier: AuthorityTier | None, restated: dict[str, Any] | None
) -> ApprovalInterruption:
    return ApprovalInterruption(
        id="x",
        created_at=datetime(2026, 6, 13, tzinfo=UTC),
        tool="delete_data",
        arguments=_ARGS,
        decision="approved",
        tier=tier,
        restated_arguments=restated,
    )


def test_restate_satisfied_tier3_matching() -> None:
    assert _restate_satisfied(_interruption(AuthorityTier.IRREVERSIBLE, dict(_ARGS)), _ARGS)


def test_restate_satisfied_tier3_missing() -> None:
    assert not _restate_satisfied(_interruption(AuthorityTier.IRREVERSIBLE, None), _ARGS)


def test_restate_satisfied_tier3_mismatched() -> None:
    assert not _restate_satisfied(
        _interruption(AuthorityTier.IRREVERSIBLE, {"path": "/other"}), _ARGS
    )


def test_restate_satisfied_lower_tiers_vacuous() -> None:
    # No restatement is required below Tier 3, with or without a tier.
    assert _restate_satisfied(_interruption(AuthorityTier.STATEFUL, None), _ARGS)
    assert _restate_satisfied(_interruption(None, None), _ARGS)


# --- end-to-end through the runtime ------------------------------------


def _model(tool: str) -> FunctionModel:
    """Propose ``tool`` once, then finish."""

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        done = sum(
            1
            for m in messages
            for p in getattr(m, "parts", [])
            if getattr(p, "part_kind", "") in ("tool-return", "retry-prompt")
        )
        if done == 0:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=tool, args=_ARGS, tool_call_id="c1")]
            )
        return ModelResponse(parts=[TextPart(content="done")])

    return FunctionModel(fn)


def _tools() -> tuple[list[Any], list[str]]:
    calls: list[str] = []

    async def delete_data(path: str) -> str:
        calls.append(path)
        return f"deleted {path}"

    async def deploy(path: str) -> str:
        calls.append(path)
        return f"deployed {path}"

    return [delete_data, deploy], calls


def _guard() -> HarnessToolGuard:
    contract: Contract[None, None] = Contract(name="c", version="0.1.0")
    return HarnessToolGuard(
        contract,
        tier_classifier=MappingTierClassifier(
            {"delete_data": AuthorityTier.IRREVERSIBLE, "deploy": AuthorityTier.STATEFUL},
            default=AuthorityTier.OBSERVE,
        ),
    )


@pytest.mark.asyncio
async def test_replay_tier3_executes_with_matching_restatement() -> None:
    tools, calls = _tools()
    rt = PydanticAIRuntime(_model("delete_data"))
    state = await rt.run("go", tools=tools, guard=_guard())
    assert isinstance(state, ResumableState)
    assert state.pending_approvals[0].tier is AuthorityTier.IRREVERSIBLE
    approved = state.approve(state.pending_approvals[0].id, restated_arguments=dict(_ARGS))
    result = await rt.run("go", tools=tools, guard=_guard(), resume=approved)
    assert not isinstance(result, ResumableState)  # ran to completion
    assert calls == ["/db"]


@pytest.mark.asyncio
async def test_replay_tier3_repauses_without_restatement() -> None:
    tools, calls = _tools()
    rt = PydanticAIRuntime(_model("delete_data"))
    state = await rt.run("go", tools=tools, guard=_guard())
    # Approve, but omit the restatement: the irreversible action must not run.
    approved = state.approve(state.pending_approvals[0].id)
    result = await rt.run("go", tools=tools, guard=_guard(), resume=approved)
    assert isinstance(result, ResumableState)  # re-paused for a fresh decision
    assert calls == []


@pytest.mark.asyncio
async def test_replay_tier3_repauses_on_mismatched_restatement() -> None:
    tools, calls = _tools()
    rt = PydanticAIRuntime(_model("delete_data"))
    state = await rt.run("go", tools=tools, guard=_guard())
    approved = state.approve(state.pending_approvals[0].id, restated_arguments={"path": "/wrong"})
    result = await rt.run("go", tools=tools, guard=_guard(), resume=approved)
    assert isinstance(result, ResumableState)
    assert calls == []


@pytest.mark.asyncio
async def test_replay_lower_tier_unchanged_by_single_step_approval() -> None:
    # A STATEFUL action still executes on a plain approve (no restatement),
    # proving the restate gate is Tier 3 only.
    tools, calls = _tools()
    rt = PydanticAIRuntime(_model("deploy"))
    state = await rt.run("go", tools=tools, guard=_guard())
    assert state.pending_approvals[0].tier is AuthorityTier.STATEFUL
    approved = state.approve(state.pending_approvals[0].id)
    result = await rt.run("go", tools=tools, guard=_guard(), resume=approved)
    assert not isinstance(result, ResumableState)
    assert calls == ["/db"]


@pytest.mark.asyncio
async def test_deferred_tier3_executes_with_matching_restatement() -> None:
    tools, calls = _tools()
    rt = PydanticAIRuntime(_model("delete_data"), approval_mode="deferred")
    state = await rt.run("go", tools=tools, guard=_guard())
    assert isinstance(state, ResumableState)
    assert state.pending_approvals[0].tier is AuthorityTier.IRREVERSIBLE
    approved = state.approve(state.pending_approvals[0].id, restated_arguments=dict(_ARGS))
    result = await rt.run("go", tools=tools, guard=_guard(), resume=approved)
    assert not isinstance(result, ResumableState)
    assert calls == ["/db"]


@pytest.mark.asyncio
async def test_deferred_tier3_repauses_without_restatement() -> None:
    tools, calls = _tools()
    rt = PydanticAIRuntime(_model("delete_data"), approval_mode="deferred")
    state = await rt.run("go", tools=tools, guard=_guard())
    approved = state.approve(state.pending_approvals[0].id)  # no restatement
    result = await rt.run("go", tools=tools, guard=_guard(), resume=approved)
    assert isinstance(result, ResumableState)
    assert calls == []
