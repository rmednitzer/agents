"""BL-248: FallbackChain graceful degradation ladder around the Runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from harness.errors import BudgetExceeded, GovernanceViolation
from harness.fallback import FallbackChain, default_should_descend
from harness.interruption import ResumableState
from harness.runtime import Runtime


class _Stub:
    """A Runtime that returns a value, or raises a configured exception."""

    def __init__(self, name: str, *, returns: Any = None, raises: Exception | None = None) -> None:
        self.name = name
        self._returns = returns
        self._raises = raises
        self.calls = 0

    async def run(self, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._returns

    def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[Any]:
        raise NotImplementedError


def test_empty_chain_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="at least one"):
        FallbackChain([])


def test_chain_is_a_runtime() -> None:
    assert isinstance(FallbackChain([_Stub("a")]), Runtime)


async def test_first_success_short_circuits() -> None:
    second = _Stub("second", returns="b")
    chain = FallbackChain([_Stub("first", returns="a"), second])
    assert await chain.run("go") == "a"
    assert second.calls == 0  # never reached


async def test_descends_on_provider_error() -> None:
    first = _Stub("first", raises=RuntimeError("provider down"))
    second = _Stub("second", returns="ok")
    chain = FallbackChain([first, second])
    assert await chain.run("go") == "ok"
    assert first.calls == 1
    assert second.calls == 1


async def test_last_exception_propagates() -> None:
    chain = FallbackChain(
        [_Stub("a", raises=RuntimeError("a")), _Stub("b", raises=ValueError("b"))]
    )
    with pytest.raises(ValueError, match="b"):
        await chain.run("go")


async def test_harness_error_does_not_descend() -> None:
    # A governance halt must not be laundered onto a backup provider.
    second = _Stub("second", returns="ok")
    chain = FallbackChain([_Stub("first", raises=GovernanceViolation("g", "tool")), second])
    with pytest.raises(GovernanceViolation):
        await chain.run("go")
    assert second.calls == 0


async def test_budget_exceeded_does_not_descend() -> None:
    second = _Stub("second", returns="ok")
    chain = FallbackChain([_Stub("first", raises=BudgetExceeded("tokens", 10.0, 11.0)), second])
    with pytest.raises(BudgetExceeded):
        await chain.run("go")
    assert second.calls == 0


async def test_approval_pause_is_returned_not_fallen_back() -> None:
    pause = ResumableState(
        contract_name="c",
        contract_version="1",
        workload="w",
        input_payload={},
        trace_id="t",
    )
    second = _Stub("second", returns="ok")
    chain = FallbackChain([_Stub("first", returns=pause), second])
    result = await chain.run("go")
    assert result is pause  # a pause is a return value, not a failure
    assert second.calls == 0


async def test_custom_should_descend_can_stop_early() -> None:
    # A predicate that refuses to descend on ValueError surfaces it.
    second = _Stub("second", returns="ok")
    chain = FallbackChain(
        [_Stub("first", raises=ValueError("nope")), second],
        should_descend=lambda exc: not isinstance(exc, ValueError),
    )
    with pytest.raises(ValueError, match="nope"):
        await chain.run("go")
    assert second.calls == 0


async def test_kwargs_threaded_to_each_attempt() -> None:
    seen: list[Any] = []

    class _Recorder:
        name = "rec"

        def __init__(self, *, ok: bool) -> None:
            self._ok = ok

        async def run(self, prompt: str, *, deps: Any = None, **kwargs: Any) -> Any:
            seen.append(deps)
            if not self._ok:
                raise RuntimeError("down")
            return "ok"

        def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[Any]:
            raise NotImplementedError

    chain = FallbackChain([_Recorder(ok=False), _Recorder(ok=True)])
    assert await chain.run("go", deps="shared") == "ok"
    assert seen == ["shared", "shared"]  # same kwargs to both attempts


async def test_stream_delegates_to_first_member() -> None:
    sentinel: list[str] = []

    class _Streamer:
        name = "s"

        async def run(self, prompt: str, **kwargs: Any) -> Any:
            return "x"

        def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[Any]:
            sentinel.append(prompt)
            return _agen()

    async def _agen() -> AsyncIterator[Any]:
        yield "chunk"

    first = _Streamer()
    chain = FallbackChain([first, _Stub("second")])
    agen = chain.stream("hello")
    assert [c async for c in agen] == ["chunk"]
    assert sentinel == ["hello"]  # delegated to the first member


def test_default_should_descend_predicate() -> None:
    assert default_should_descend(RuntimeError("x")) is True
    assert default_should_descend(ValueError("x")) is True
    assert default_should_descend(GovernanceViolation("g", "t")) is False
    assert default_should_descend(BudgetExceeded("tokens", 1.0, 2.0)) is False
