"""BL-132 / BL-171: model_settings pass-through and cache-token surfacing.

Deterministic and network-free (TestModel / FunctionModel, ADR 0001):
these tests assert the wiring (settings reach the model call) and the
accounting (cache counts reach the tracker, max_tokens semantics
unchanged). Whether the provider actually serves a cache hit is
observable only against a live API and stays coupled to the BL-120
live-workload gate (ADR 0026).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage

from harness.budgets import ActionBudget, BudgetTracker
from harness.errors import BudgetExceeded
from harness.runtime import PydanticAIRuntime

CACHE_SETTINGS = {
    "anthropic_cache_instructions": True,
    "anthropic_cache_tool_definitions": True,
}


def _settings_probe(seen: list[Any]) -> FunctionModel:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(info.model_settings)
        return ModelResponse(parts=[TextPart(content="ok")])

    return FunctionModel(fn)


def _cached_usage_model(read: int, write: int) -> FunctionModel:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content="done")],
            usage=RequestUsage(
                input_tokens=10,
                output_tokens=4,
                cache_read_tokens=read,
                cache_write_tokens=write,
            ),
        )

    return FunctionModel(fn)


# --- model_settings pass-through ---------------------------------------


async def test_model_settings_reach_the_model_call() -> None:
    seen: list[Any] = []
    rt = PydanticAIRuntime(_settings_probe(seen), model_settings=CACHE_SETTINGS)
    out = await rt.run("p")
    assert out == "ok"
    assert seen == [CACHE_SETTINGS]


async def test_default_keeps_no_model_settings() -> None:
    seen: list[Any] = []
    rt = PydanticAIRuntime(_settings_probe(seen))
    await rt.run("p")
    assert seen == [None]


async def test_model_settings_forwarded_in_stream_mode() -> None:
    seen: list[Any] = []

    async def sf(messages: list[ModelMessage], info: AgentInfo) -> Any:
        seen.append(info.model_settings)
        yield "ok"

    rt = PydanticAIRuntime(FunctionModel(stream_function=sf), model_settings=CACHE_SETTINGS)
    chunks = [c async for c in rt.stream("p")]
    assert "".join(chunks) == "ok"
    assert seen == [CACHE_SETTINGS]


# --- cache-token surfacing in run() ------------------------------------


async def test_cache_tokens_surface_to_tracker() -> None:
    rt = PydanticAIRuntime(_cached_usage_model(read=70, write=30))
    tracker = BudgetTracker(ActionBudget())
    out = await rt.run("p", budget=tracker)
    assert out == "done"
    assert tracker.cache_read_tokens == 70
    assert tracker.cache_write_tokens == 30
    # max_tokens accounting is unchanged: input + output only.
    assert tracker.tokens == 14


async def test_cache_tokens_not_charged_to_max_tokens() -> None:
    rt = PydanticAIRuntime(_cached_usage_model(read=1000, write=1000))
    tracker = BudgetTracker(ActionBudget(max_tokens=20))
    out = await rt.run("p", budget=tracker)
    assert out == "done"
    assert tracker.tokens == 14
    assert tracker.cache_read_tokens == 1000


async def test_uncached_usage_leaves_counters_zero() -> None:
    rt = PydanticAIRuntime(TestModel(custom_output_text="plain"))
    tracker = BudgetTracker(ActionBudget())
    await rt.run("p", budget=tracker)
    assert tracker.cache_read_tokens == 0
    assert tracker.cache_write_tokens == 0


async def test_usage_object_without_cache_fields_is_tolerated() -> None:
    # The _usage compat stance: a custom double whose usage lacks the
    # cache attributes entirely must surface nothing rather than crash.
    class _BareUsage:
        input_tokens = 3
        output_tokens = 2
        requests = 1

    class _Result:
        usage = _BareUsage()
        output = "bare"

    class _Runtimeish(PydanticAIRuntime):
        async def run(self, prompt: str, **kwargs: Any) -> Any:  # type: ignore[override]
            from harness.runtime import _surface_cache_tokens, _usage

            budget = kwargs["budget"]
            usage = _usage(_Result())
            _surface_cache_tokens(budget, usage)
            return _Result().output

    tracker = BudgetTracker(ActionBudget())
    out = await _Runtimeish(TestModel()).run("p", budget=tracker)
    assert out == "bare"
    assert tracker.cache_read_tokens == 0
    assert tracker.cache_write_tokens == 0


# --- cache-token surfacing in stream() ---------------------------------


async def test_stream_completes_with_zero_cache_counters_on_testmodel() -> None:
    rt = PydanticAIRuntime(TestModel(custom_output_text="abc"))
    tracker = BudgetTracker(ActionBudget())
    chunks = [c async for c in rt.stream("p", budget=tracker)]
    assert "".join(chunks) == "abc"
    assert tracker.cache_read_tokens == 0
    assert tracker.cache_write_tokens == 0


async def test_stream_invokes_cache_surfacing_at_final_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pins that stream() calls the surfacing hook exactly once, with
    # the run's tracker, at the final reconciliation: a refactor that
    # drops the call in stream() fails here even though TestModel
    # reports zero cache counts (no public stream API injects them).
    # The recording wrapper calls through, so the real path still runs.
    import harness.runtime as runtime_module

    real = runtime_module._surface_cache_tokens
    calls: list[tuple[Any, Any]] = []

    def recording(budget: Any, usage: Any) -> None:
        calls.append((budget, usage))
        real(budget, usage)

    monkeypatch.setattr(runtime_module, "_surface_cache_tokens", recording)
    rt = PydanticAIRuntime(TestModel(custom_output_text="abc"))
    tracker = BudgetTracker(ActionBudget())
    chunks = [c async for c in rt.stream("p", budget=tracker)]
    assert "".join(chunks) == "abc"
    assert len(calls) == 1
    assert calls[0][0] is tracker
    # The surfaced object is the stream's own usage: feeding it back
    # through the real hook is idempotent for zero counts.
    assert tracker.cache_read_tokens == 0
    assert tracker.cache_write_tokens == 0


# --- BudgetTracker.consume_cache_tokens contract ------------------------


def test_consume_cache_tokens_accumulates() -> None:
    tracker = BudgetTracker(ActionBudget())
    tracker.consume_cache_tokens(read=5, write=2)
    tracker.consume_cache_tokens(read=3)
    assert tracker.cache_read_tokens == 8
    assert tracker.cache_write_tokens == 2


def test_consume_cache_tokens_zero_is_noop() -> None:
    tracker = BudgetTracker(ActionBudget())
    tracker.consume_cache_tokens()
    assert tracker.cache_read_tokens == 0
    assert tracker.cache_write_tokens == 0


@pytest.mark.parametrize(
    ("read", "write"),
    [(-1, 0), (0, -1), (-5, -5)],
)
def test_consume_cache_tokens_rejects_negative(read: int, write: int) -> None:
    tracker = BudgetTracker(ActionBudget())
    with pytest.raises(ValueError, match="non-negative"):
        tracker.consume_cache_tokens(read=read, write=write)


def test_cache_counters_never_trip_a_budget() -> None:
    # No ceiling exists for the cache dimension: surfacing is pure
    # accounting, so even absurd counts raise nothing.
    tracker = BudgetTracker(ActionBudget(max_tokens=1, max_cost_usd=0.0))
    tracker.consume_cache_tokens(read=10**9, write=10**9)
    assert tracker.cache_read_tokens == 10**9


def test_snapshot_keys_unchanged_by_cache_counters() -> None:
    # BL-154 resume-surface regression pin: the snapshot carries exactly
    # the enforced dimensions; cache counters are deliberately absent
    # (no ceiling to carry across a pause).
    tracker = BudgetTracker(ActionBudget())
    tracker.consume_cache_tokens(read=9, write=9)
    assert set(tracker.snapshot()) == {
        "consumed_steps",
        "consumed_tokens",
        "consumed_tool_calls",
        "consumed_per_tool",
        "consumed_per_tool_tokens",
        "consumed_per_tool_seconds",
        "consumed_cost_usd",
    }


async def test_budget_exceeded_on_real_tokens_still_fires_with_cache_present() -> None:
    # Cache fields must not mask a genuine input+output overflow.
    rt = PydanticAIRuntime(_cached_usage_model(read=50, write=0))
    tracker = BudgetTracker(ActionBudget(max_tokens=5))
    with pytest.raises(BudgetExceeded) as exc_info:
        await rt.run("p", budget=tracker)
    assert exc_info.value.budget_kind == "tokens"
