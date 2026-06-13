"""BL-253: evidence-capture hook around an irreversible action's execution.

The held-out behavioural half of BL-252 (ADR 0033), delivered as ADR
0038. A workload-supplied ``EvidenceHook`` brackets an approved Tier 3
(IRREVERSIBLE) tool body: ``before`` immediately before it runs and
``after`` immediately after (in a ``finally``, with the body's exception
or ``None``). It fires only for an IRREVERSIBLE action and only when a
hook is configured; every other call is unchanged. Identical across the
replay, deferred, and MCP paths (the shared ``_with_evidence`` helper).
Deterministic and network-free (FunctionModel, ADR 0001).
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from harness.authority import (
    AuthorityTier,
    MappingRollbackPlanner,
    MappingTierClassifier,
)
from harness.contract import Contract
from harness.evidence import (
    EvidenceContext,
    EvidenceHook,
    EvidenceRecord,
    RecordingEvidenceHook,
)
from harness.guard import HarnessToolGuard
from harness.interruption import ResumableState
from harness.runtime import PydanticAIRuntime, _GateResult, _with_evidence

_ARGS = {"path": "/db"}


# --- the reference hook and context (unit) -----------------------------


def test_evidence_context_is_frozen() -> None:
    ctx = EvidenceContext(tool="delete", arguments={}, tier=AuthorityTier.IRREVERSIBLE)
    with pytest.raises(FrozenInstanceError):
        ctx.tool = "other"  # type: ignore[misc]


def test_recording_hook_satisfies_protocol() -> None:
    assert isinstance(RecordingEvidenceHook(), EvidenceHook)


async def test_recording_hook_pairs_after_to_before_via_token() -> None:
    hook = RecordingEvidenceHook()
    ctx = EvidenceContext(
        tool="delete",
        arguments=_ARGS,
        tier=AuthorityTier.IRREVERSIBLE,
        tool_call_id="c1",
        rollback_plan="restore",
    )
    token = await hook.before(ctx)
    await hook.after(token, error=None)
    assert hook.records == [
        EvidenceRecord(
            phase="before",
            tool="delete",
            tier=AuthorityTier.IRREVERSIBLE,
            tool_call_id="c1",
            rollback_plan="restore",
        ),
        EvidenceRecord(
            phase="after",
            tool="delete",
            tier=AuthorityTier.IRREVERSIBLE,
            tool_call_id="c1",
            rollback_plan="restore",
        ),
    ]


# --- _with_evidence (unit) ---------------------------------------------


def _gate(tier: AuthorityTier | None) -> _GateResult:
    plan = "undo" if tier is AuthorityTier.IRREVERSIBLE else None
    return _GateResult(soft=None, tier=tier, rollback_plan=plan)


async def test_with_evidence_no_hook_runs_body_unbracketed() -> None:
    async def _run() -> str:
        return "ok"

    out = await _with_evidence(
        None, _gate(AuthorityTier.IRREVERSIBLE), tool="t", arguments={}, tool_call_id=None, run=_run
    )
    assert out == "ok"


async def test_with_evidence_lower_tier_skips_hook() -> None:
    hook = RecordingEvidenceHook()

    async def _run() -> str:
        return "ok"

    out = await _with_evidence(
        hook, _gate(AuthorityTier.STATEFUL), tool="t", arguments={}, tool_call_id=None, run=_run
    )
    assert out == "ok"
    assert hook.records == []


async def test_with_evidence_none_tier_skips_hook() -> None:
    hook = RecordingEvidenceHook()

    async def _run() -> str:
        return "ok"

    out = await _with_evidence(
        hook, _GateResult(), tool="t", arguments={}, tool_call_id=None, run=_run
    )
    assert out == "ok"
    assert hook.records == []


async def test_with_evidence_tier3_brackets_body() -> None:
    hook = RecordingEvidenceHook()

    async def _run() -> str:
        return "result"

    out = await _with_evidence(
        hook,
        _gate(AuthorityTier.IRREVERSIBLE),
        tool="delete",
        arguments=_ARGS,
        tool_call_id="c9",
        run=_run,
    )
    assert out == "result"
    assert [r.phase for r in hook.records] == ["before", "after"]
    assert hook.records[0].tool == "delete"
    assert hook.records[0].tool_call_id == "c9"
    assert hook.records[0].rollback_plan == "undo"
    assert hook.records[1].error is None


async def test_with_evidence_records_body_failure_and_reraises() -> None:
    hook = RecordingEvidenceHook()

    async def _run() -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await _with_evidence(
            hook,
            _gate(AuthorityTier.IRREVERSIBLE),
            tool="delete",
            arguments={},
            tool_call_id=None,
            run=_run,
        )
    assert [r.phase for r in hook.records] == ["before", "after"]
    assert "boom" in (hook.records[1].error or "")


async def test_with_evidence_concurrent_calls_pair_by_token() -> None:
    # The token, not a shared field, pairs each after to its before, so
    # concurrent Tier 3 bodies do not cross-contaminate.
    hook = RecordingEvidenceHook()
    gate = _gate(AuthorityTier.IRREVERSIBLE)

    async def call(tool: str, value: str) -> str:
        async def _run() -> str:
            await asyncio.sleep(0)  # yield, forcing interleave
            return value

        return await _with_evidence(
            hook, gate, tool=tool, arguments={}, tool_call_id=None, run=_run
        )

    a, b = await asyncio.gather(call("delete_a", "ra"), call("delete_b", "rb"))
    assert {a, b} == {"ra", "rb"}
    assert sorted(r.tool for r in hook.records if r.phase == "before") == ["delete_a", "delete_b"]
    # Each after copies its paired before's tool through the token: an
    # after labelled delete_a proves it resolved delete_a's before.
    assert sorted(r.tool for r in hook.records if r.phase == "after") == ["delete_a", "delete_b"]


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
        rollback_planner=MappingRollbackPlanner({"delete_data": "restore /db from snapshot"}),
    )


@pytest.mark.asyncio
async def test_replay_tier3_fires_evidence_around_body() -> None:
    tools, calls = _tools()
    hook = RecordingEvidenceHook()
    rt = PydanticAIRuntime(_model("delete_data"), evidence_hook=hook)
    state = await rt.run("go", tools=tools, guard=_guard())
    assert isinstance(state, ResumableState)
    # The pre-approval pause runs no body, so no evidence yet.
    assert hook.records == []
    approved = state.approve(state.pending_approvals[0].id, restated_arguments=dict(_ARGS))
    result = await rt.run("go", tools=tools, guard=_guard(), resume=approved)
    assert not isinstance(result, ResumableState)
    assert calls == ["/db"]
    assert [r.phase for r in hook.records] == ["before", "after"]
    assert hook.records[0].tool == "delete_data"
    assert hook.records[0].tier is AuthorityTier.IRREVERSIBLE
    assert hook.records[0].rollback_plan == "restore /db from snapshot"
    assert hook.records[0].tool_call_id is None  # replay-local has no per-call id
    assert hook.records[1].error is None


@pytest.mark.asyncio
async def test_replay_lower_tier_fires_no_evidence() -> None:
    # A STATEFUL action runs after a single-step approve, but the hook
    # only fires for Tier 3.
    tools, calls = _tools()
    hook = RecordingEvidenceHook()
    rt = PydanticAIRuntime(_model("deploy"), evidence_hook=hook)
    state = await rt.run("go", tools=tools, guard=_guard())
    approved = state.approve(state.pending_approvals[0].id)
    result = await rt.run("go", tools=tools, guard=_guard(), resume=approved)
    assert not isinstance(result, ResumableState)
    assert calls == ["/db"]
    assert hook.records == []


@pytest.mark.asyncio
async def test_replay_tier3_records_body_failure() -> None:
    hook = RecordingEvidenceHook()

    async def delete_data(path: str) -> str:
        raise RuntimeError("disk gone")

    tools = [delete_data]
    rt = PydanticAIRuntime(_model("delete_data"), evidence_hook=hook)
    state = await rt.run("go", tools=tools, guard=_guard())
    approved = state.approve(state.pending_approvals[0].id, restated_arguments=dict(_ARGS))
    with pytest.raises(Exception):  # noqa: B017, PT011 - framework may reshape the in-tool error
        await rt.run("go", tools=tools, guard=_guard(), resume=approved)
    # after() ran in the finally before the exception unwound, so the
    # failed irreversible action is still on the audit trail.
    assert [r.phase for r in hook.records] == ["before", "after"]
    assert "disk gone" in (hook.records[1].error or "")


@pytest.mark.asyncio
async def test_deferred_tier3_fires_evidence_once() -> None:
    tools, calls = _tools()
    hook = RecordingEvidenceHook()
    rt = PydanticAIRuntime(_model("delete_data"), approval_mode="deferred", evidence_hook=hook)
    state = await rt.run("go", tools=tools, guard=_guard())
    assert isinstance(state, ResumableState)
    approved = state.approve(state.pending_approvals[0].id, restated_arguments=dict(_ARGS))
    result = await rt.run("go", tools=tools, guard=_guard(), resume=approved)
    assert not isinstance(result, ResumableState)
    assert calls == ["/db"]  # body ran exactly once
    assert [r.phase for r in hook.records] == ["before", "after"]
    assert hook.records[0].tool == "delete_data"
    assert hook.records[0].tool_call_id is not None  # deferred carries a per-call id
