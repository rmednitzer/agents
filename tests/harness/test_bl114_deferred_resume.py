"""BL-114: deferred (non-replay) approval resume on the runtime adapter.

Deterministic and network-free (FunctionModel, ADR 0001). The headline
assertion is non-replay: a side-effect tool executed before the pause
runs exactly once across pause and resume, because the resumed leg
continues from the paused leg's message history
(`ResumableState.runtime_state`) instead of re-running the agent
(ADR 0027). Replay mode stays the byte-identical default.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from harness.budgets import ActionBudget, BudgetTracker
from harness.errors import GovernanceViolation, HarnessError
from harness.guard import GuardDecision, GuardResponse
from harness.interruption import ResumableState
from harness.runtime import PydanticAIRuntime


class _SelectiveGuard:
    """REQUIRE_APPROVAL for the named tools, APPROVE for the rest."""

    def __init__(self, gated: set[str], *, hard_reject: set[str] | None = None) -> None:
        self._gated = gated
        self._hard = hard_reject or set()

    async def check(self, tool: str, arguments: dict[str, Any]) -> GuardResponse:
        from harness.contract import Severity

        if tool in self._hard:
            return GuardResponse(
                decision=GuardDecision.REJECT, reason="forbidden", severity=Severity.HARD
            )
        if tool in self._gated:
            return GuardResponse(decision=GuardDecision.REQUIRE_APPROVAL)
        return GuardResponse(decision=GuardDecision.APPROVE)


def _completed_parts(messages: list[ModelMessage]) -> int:
    return sum(
        1
        for m in messages
        for p in getattr(m, "parts", [])
        if getattr(p, "part_kind", "") in ("tool-return", "retry-prompt")
    )


def _two_step_model(seen_messages: list[list[ModelMessage]] | None = None) -> FunctionModel:
    """Propose record(), then deploy(), then finish."""

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen_messages is not None:
            seen_messages.append(messages)
        done = _completed_parts(messages)
        if done == 0:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="record", args={"note": "step1"}, tool_call_id="r1")]
            )
        if done == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="deploy", args={"env": "prod"}, tool_call_id="d1")]
            )
        return ModelResponse(parts=[TextPart(content="final")])

    return FunctionModel(fn)


def _tools() -> tuple[list[Any], list[str], list[str]]:
    record_calls: list[str] = []
    deploy_calls: list[str] = []

    async def record(note: str) -> str:
        record_calls.append(note)
        return "recorded"

    async def deploy(env: str) -> str:
        deploy_calls.append(env)
        return f"deployed {env}"

    return [record, deploy], record_calls, deploy_calls


def test_invalid_approval_mode_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="approval_mode"):
        PydanticAIRuntime("test", approval_mode="bogus")


async def test_deferred_pause_returns_state_without_executing_gated_tool() -> None:
    tools, record_calls, deploy_calls = _tools()
    rt = PydanticAIRuntime(_two_step_model(), approval_mode="deferred")
    tracker = BudgetTracker(ActionBudget())
    state = await rt.run("go", tools=tools, guard=_SelectiveGuard({"deploy"}), budget=tracker)
    assert isinstance(state, ResumableState)
    assert record_calls == ["step1"]
    assert deploy_calls == []
    [pending] = state.pending_approvals
    assert pending.tool == "deploy"
    assert pending.arguments == {"env": "prod"}
    assert pending.id == "d1"
    assert state.runtime_state is not None
    assert state.runtime_state["mode"] == "deferred"
    # The continuation state must survive persistence.
    json.dumps(state.runtime_state)
    # The paused leg's spend is real and charged (tokens, the record
    # call), unlike a replay-mode pause which aborts without usage.
    assert tracker.tokens > 0
    assert tracker.tool_calls == 1


async def test_deferred_approve_resume_continues_without_replay() -> None:
    tools, record_calls, deploy_calls = _tools()
    rt = PydanticAIRuntime(_two_step_model(), approval_mode="deferred")
    guard = _SelectiveGuard({"deploy"})
    state = await rt.run("go", tools=tools, guard=guard)
    assert isinstance(state, ResumableState)
    approved = state.approve(state.pending_approvals[0].id)

    tracker = BudgetTracker(ActionBudget())
    out = await rt.run("go", tools=tools, guard=guard, resume=approved, budget=tracker)
    assert out == "final"
    # Non-replay, the BL-114 headline: the pre-pause side effect did
    # not re-execute on the resumed leg.
    assert record_calls == ["step1"]
    assert deploy_calls == ["prod"]
    # The resumed leg charged the approved call.
    assert tracker.tool_calls == 1


async def test_deferred_deny_resume_is_model_visible_and_continues() -> None:
    tools, record_calls, deploy_calls = _tools()
    seen: list[list[ModelMessage]] = []
    rt = PydanticAIRuntime(_two_step_model(seen), approval_mode="deferred")
    guard = _SelectiveGuard({"deploy"})
    state = await rt.run("go", tools=tools, guard=guard)
    assert isinstance(state, ResumableState)
    denied = state.deny(state.pending_approvals[0].id, reason="not allowed by operator")

    out = await rt.run("go", tools=tools, guard=guard, resume=denied)
    # No ApprovalDenied raise: the model saw the denial and finished
    # (the deliberate semantic divergence from replay mode, ADR 0027).
    assert out == "final"
    assert deploy_calls == []
    assert record_calls == ["step1"]
    flattened = "".join(
        str(getattr(p, "content", "")) for m in seen[-1] for p in getattr(m, "parts", [])
    )
    assert "not allowed by operator" in flattened


async def test_deferred_approval_for_different_arguments_repauses() -> None:
    tools, _record_calls, deploy_calls = _tools()
    rt = PydanticAIRuntime(_two_step_model(), approval_mode="deferred")
    guard = _SelectiveGuard({"deploy"})
    state = await rt.run("go", tools=tools, guard=guard)
    assert isinstance(state, ResumableState)
    # Tamper: the recorded approval claims different arguments than
    # the call in the history. The (tool, arguments) binding (BL-193)
    # must refuse to execute and re-pause for a fresh decision.
    tampered_ai = state.pending_approvals[0].model_copy(
        update={"decision": "approved", "arguments": {"env": "staging"}}
    )
    tampered = state.model_copy(update={"pending_approvals": [tampered_ai]})

    out = await rt.run("go", tools=tools, guard=guard, resume=tampered)
    assert isinstance(out, ResumableState)
    assert deploy_calls == []
    [pending] = out.pending_approvals
    assert pending.tool == "deploy"
    assert pending.arguments == {"env": "prod"}


async def test_deferred_resume_requires_a_decision_for_every_approval() -> None:
    tools, _r, _d = _tools()
    rt = PydanticAIRuntime(_two_step_model(), approval_mode="deferred")
    guard = _SelectiveGuard({"deploy"})
    state = await rt.run("go", tools=tools, guard=guard)
    assert isinstance(state, ResumableState)
    with pytest.raises(HarnessError, match="undecided"):
        await rt.run("go", tools=tools, guard=guard, resume=state)


async def test_deferred_resume_rejects_replay_shaped_state() -> None:
    rt = PydanticAIRuntime(_two_step_model(), approval_mode="deferred")
    replay_shaped = ResumableState(
        contract_name="c",
        contract_version="",
        workload="w",
        input_payload={"prompt": "go"},
        trace_id="t",
    )
    with pytest.raises(HarnessError, match="deferred"):
        await rt.run("go", resume=replay_shaped)


async def test_replay_default_still_pauses_with_no_runtime_state() -> None:
    tools, _record_calls, deploy_calls = _tools()
    rt = PydanticAIRuntime(_two_step_model())
    state = await rt.run("go", tools=tools, guard=_SelectiveGuard({"deploy"}))
    assert isinstance(state, ResumableState)
    assert state.runtime_state is None
    assert deploy_calls == []


async def test_deferred_hard_reject_unchanged() -> None:
    tools, _record_calls, _deploy_calls = _tools()
    rt = PydanticAIRuntime(_two_step_model(), approval_mode="deferred")
    guard = _SelectiveGuard(set(), hard_reject={"record"})
    with pytest.raises(GovernanceViolation):
        await rt.run("go", tools=tools, guard=guard)


async def test_deferred_second_pause_carries_trace_and_prompt() -> None:
    # record -> deploy (gated) -> wipe (gated) -> final: the re-pause
    # after the first resume keeps the original trace_id and prompt.
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        done = _completed_parts(messages)
        if done == 0:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="record", args={"note": "s"}, tool_call_id="r1")]
            )
        if done == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="deploy", args={"env": "prod"}, tool_call_id="d1")]
            )
        if done == 2:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="wipe", args={"db": "main"}, tool_call_id="w1")]
            )
        return ModelResponse(parts=[TextPart(content="final")])

    tools, _record_calls, deploy_calls = _tools()
    wipe_calls: list[str] = []

    async def wipe(db: str) -> str:
        wipe_calls.append(db)
        return "wiped"

    all_tools = [*tools, wipe]
    rt = PydanticAIRuntime(FunctionModel(fn), approval_mode="deferred")
    guard = _SelectiveGuard({"deploy", "wipe"})

    first = await rt.run("go", tools=all_tools, guard=guard)
    assert isinstance(first, ResumableState)
    assert [ai.tool for ai in first.pending_approvals] == ["deploy"]

    second = await rt.run(
        "ignored on resume",
        tools=all_tools,
        guard=guard,
        resume=first.approve("d1"),
    )
    assert isinstance(second, ResumableState)
    assert [ai.tool for ai in second.pending_approvals] == ["wipe"]
    assert second.trace_id == first.trace_id
    assert second.input_payload == first.input_payload
    assert deploy_calls == ["prod"]

    final = await rt.run("ignored again", tools=all_tools, guard=guard, resume=second.approve("w1"))
    assert final == "final"
    assert wipe_calls == ["main"]
    assert deploy_calls == ["prod"]


async def test_deferred_mode_without_gated_tools_behaves_normally() -> None:
    tools, record_calls, deploy_calls = _tools()
    rt = PydanticAIRuntime(_two_step_model(), approval_mode="deferred")
    out = await rt.run("go", tools=tools, guard=_SelectiveGuard(set()))
    assert out == "final"
    assert record_calls == ["step1"]
    assert deploy_calls == ["prod"]


async def test_deferred_budget_accumulates_across_legs_via_snapshot() -> None:
    # The BL-154 flow, unchanged by deferred mode: the caller threads
    # the paused tracker's snapshot into the resumed tracker.
    tools, _r, _d = _tools()
    rt = PydanticAIRuntime(_two_step_model(), approval_mode="deferred")
    guard = _SelectiveGuard({"deploy"})
    leg1 = BudgetTracker(ActionBudget())
    state = await rt.run("go", tools=tools, guard=guard, budget=leg1)
    assert isinstance(state, ResumableState)
    snap = leg1.snapshot()
    leg2 = BudgetTracker(
        ActionBudget(),
        initial_steps=snap["consumed_steps"],
        initial_tokens=snap["consumed_tokens"],
        initial_tool_calls=snap["consumed_tool_calls"],
        initial_per_tool=snap["consumed_per_tool"],
    )
    out = await rt.run("go", tools=tools, guard=guard, resume=state.approve("d1"), budget=leg2)
    assert out == "final"
    assert leg2.tool_calls == leg1.tool_calls + 1  # record (leg 1) + deploy (leg 2)
    assert leg2.tokens > leg1.tokens
