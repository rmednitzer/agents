"""Graceful degradation ladder around the Runtime Protocol (BL-248).

`RetryPolicy` (BL-136) retries the *same* call against the *same*
provider with backoff and a circuit breaker, which is resilience against
a transient blip. It is not a *fallback ladder*: when a provider is down
(not just flaky), or a premium model is unavailable, the operator-gateway
pattern degrades to the next option (a cheaper model, a cached path, a
local stub) so the pipeline returns an answer rather than failing.

`FallbackChain` is that ladder, expressed as a `Runtime` that wraps an
ordered list of `Runtime`s. It composes with `RetryPolicy` rather than
replacing it: each member runtime owns its own retry/backoff, and the
chain only descends to the next member once a member has exhausted its
own resilience and raised.

The descend boundary is deliberate. A deliberate policy halt (a
`HarnessError`: governance reject, budget exceeded, approval denied) must
*not* fall through to another provider, or the chain would launder a
governed-away call onto a backup model. So the default `should_descend`
descends on any `Exception` that is not a `HarnessError`, and a
`BaseException` (`KeyboardInterrupt` / `SystemExit` / `CancelledError`)
always propagates (the BL-165 invariant). An approval pause is a return
value (`ResumableState`), not an exception, so it is returned as-is and
never triggers a fallback.

This is a per-run composition, not a contract change: `run_under_contract`
takes any `Runtime`, so a `FallbackChain` drops in unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from harness.errors import HarnessError
from harness.runtime import Runtime

__all__ = ["FallbackChain", "default_should_descend"]


def default_should_descend(exc: Exception) -> bool:
    """Descend to the next runtime on a provider/transient failure.

    True for any ``Exception`` that is not a ``HarnessError``: a network
    error, a provider API error, a parse error are all worth a fallback.
    A ``HarnessError`` (governance / budget / approval halt) is a
    deliberate decision and returns False, so the chain never reroutes a
    governed-away or budget-exceeded call to a backup provider.
    """
    return not isinstance(exc, HarnessError)


class FallbackChain:
    """A `Runtime` that tries an ordered list of runtimes in turn (BL-248).

    ``run`` calls each member until one returns; if a member raises and
    ``should_descend`` accepts the exception and another member remains,
    the chain tries the next. If ``should_descend`` rejects the exception,
    or the last member raises, that exception propagates. The same
    ``budget`` (and every other kwarg) is threaded to each attempt, so a
    failed attempt's spend still counts against the run's budget.

    ``stream`` delegates to the first member only: there is no mid-stream
    fallback (a partially-streamed response cannot be cleanly retried on
    another provider), documented rather than faked.
    """

    def __init__(
        self,
        runtimes: list[Runtime],
        *,
        should_descend: Callable[[Exception], bool] = default_should_descend,
        name: str = "fallback",
    ) -> None:
        # Load-time validation (ADR 0007): an empty ladder has nothing to
        # run and is a construction error, not a mid-run surprise.
        if not runtimes:
            raise ValueError("FallbackChain requires at least one runtime")
        self._runtimes = list(runtimes)
        self._should_descend = should_descend
        self.name = name

    async def run(self, prompt: str, **kwargs: Any) -> Any:
        last = len(self._runtimes) - 1
        for index, runtime in enumerate(self._runtimes):
            try:
                return await runtime.run(prompt, **kwargs)
            except Exception as exc:
                # The last member, or an exception the predicate rejects
                # (a HarnessError by default), propagates unchanged.
                if index == last or not self._should_descend(exc):
                    raise
                # Otherwise descend to the next member.
        # Unreachable: the loop either returns or raises on the last
        # member, but keeps the type checker satisfied about the return.
        raise AssertionError("FallbackChain exhausted without returning")  # pragma: no cover

    def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[Any]:
        """Delegate to the first member; streaming has no fallback."""
        return self._runtimes[0].stream(prompt, **kwargs)
