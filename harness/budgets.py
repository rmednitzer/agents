"""Action budgets for the harness.

ActionBudget is an immutable spec. BudgetTracker is the per-run mutable
counter (the only mutable harness object). The runtime adapter is
responsible for calling consume_step / consume_tokens / consume_tool_call
/ check_wall_clock at the right points; the tracker raises BudgetExceeded
(and emits BudgetExceededEvent) on overflow.

Wall-clock enforcement is reactive: callers must invoke check_wall_clock
at known checkpoints (e.g. before each step). Background timing is out of
scope for L1.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from harness.errors import BudgetExceeded
from harness.events import BudgetExceededEvent
from harness.sinks import EventSink, NullSink

__all__ = [
    "ActionBudget",
    "BudgetKind",
    "BudgetTracker",
]

BudgetKind = Literal["steps", "tokens", "wall_clock", "tool_calls", "cost"]


def _validate_float_limit(field: str, value: float | None) -> None:
    """Reject a non-finite or negative float *limit* (BL-231).

    The dual of BL-221: that fix hardened the *consumed* side of every
    ``consumed > limit`` check (the caller-fed floats into the tracker);
    the *limit* side (the spec) was unvalidated. A ``NaN`` or ``+inf``
    limit makes ``consumed > limit`` always False, so the ceiling is
    silently disabled for the whole run, the exact NaN-comparison trap
    of BL-159 / BL-205 / BL-221 / BL-226. ``None`` (unlimited) and
    ``0`` (a zero ceiling) stay valid.
    """
    if value is None:
        return
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite when set (got {value!r})")
    if value < 0:
        raise ValueError(f"{field} must be non-negative when set (got {value!r})")


def _validate_int_limit(field: str, value: int | None) -> None:
    """Reject a negative integer *limit* (BL-231).

    Integers cannot be NaN / inf, so only the negative case applies; a
    negative count limit is a meaningless spec. ``None`` (unlimited) and
    ``0`` (a zero ceiling) stay valid.
    """
    if value is not None and value < 0:
        raise ValueError(f"{field} must be non-negative when set (got {value!r})")


class ActionBudget(BaseModel):
    """Immutable action budget spec.

    All fields are optional; None means unlimited for that dimension.

    ``max_tool_calls`` is the aggregate cap across every tool.
    ``max_tool_calls_per_tool`` (BL-073) caps individual tools, e.g.
    ``{"search": 3, "delete": 1}``; a tool absent from the map is
    bounded only by the aggregate cap. Both are enforced.

    L3 cost and per-tool resource caps (BL-123), all optional and
    defaulting to None so an existing ActionBudget is unchanged. What
    the default ``PydanticAIRuntime`` feeds, and what is caller-fed,
    differs by dimension (the framework binds no model or pricing, ADR
    0001, and PydanticAI reports token usage at the run level, not per
    tool):

    - ``max_cost_usd``: aggregate spend ceiling. The framework prices
      no model, so the default adapter never calls ``consume_cost``;
      this dimension stays 0 unless a pricing-aware caller or a
      pricing-aware adapter feeds spend via ``consume_cost`` (the same
      "caller-fed" stance as ``RuntimeSpec.parameters`` not being
      auto-forwarded). It is the surface for cost capping, not an
      automatic cost meter.
    - ``max_wall_clock_seconds_per_tool``: per-tool wall-clock ceiling.
      The default adapter DOES feed this: it times each tool body and
      attributes the duration, so this cap fires in a real run.
    - ``max_tokens_per_tool``: per-tool token ceiling. A tool call does
      not itself consume model tokens (the model round-trips do), so
      the default adapter has no per-tool token signal and never feeds
      this; it is caller-fed (a tool that itself calls a model can pass
      ``tokens=`` to ``consume_tool_call``).

    A tool absent from a per-tool map is bounded only by the aggregate
    cap for that dimension.
    """

    model_config = ConfigDict(frozen=True)

    max_steps: int | None = None
    max_tokens: int | None = None
    max_wall_clock_seconds: float | None = None
    max_tool_calls: int | None = None
    max_tool_calls_per_tool: dict[str, int] | None = None
    max_cost_usd: float | None = None
    max_tokens_per_tool: dict[str, int] | None = None
    max_wall_clock_seconds_per_tool: dict[str, float] | None = None

    @model_validator(mode="after")
    def _check_limits(self) -> ActionBudget:
        """Reject a non-finite or negative limit at construction (BL-231).

        BL-221 validated the floats a caller *consumes* into the tracker;
        this validates the *limits* the spec declares, closing the dual.
        Surfacing the error at construction (not mid-run) matches the
        ADR 0007 "configuration errors at load time" rule and the
        ``Namespace.resolve_ttl`` (BL-197) / ``MultiDispatcher`` weight
        (BL-205) precedents. A ``None`` (unlimited) or ``0`` (zero
        ceiling) limit stays valid, so every existing budget is
        unaffected; only a NaN / +inf / negative spec is rejected.
        """
        _validate_int_limit("max_steps", self.max_steps)
        _validate_int_limit("max_tokens", self.max_tokens)
        _validate_int_limit("max_tool_calls", self.max_tool_calls)
        _validate_float_limit("max_wall_clock_seconds", self.max_wall_clock_seconds)
        _validate_float_limit("max_cost_usd", self.max_cost_usd)
        for tool, call_cap in (self.max_tool_calls_per_tool or {}).items():
            _validate_int_limit(f"max_tool_calls_per_tool[{tool!r}]", call_cap)
        for tool, token_cap in (self.max_tokens_per_tool or {}).items():
            _validate_int_limit(f"max_tokens_per_tool[{tool!r}]", token_cap)
        for tool, sec_cap in (self.max_wall_clock_seconds_per_tool or {}).items():
            _validate_float_limit(f"max_wall_clock_seconds_per_tool[{tool!r}]", sec_cap)
        return self


class BudgetTracker:
    """Mutable per-run budget counter.

    Constructed by the harness at the start of a run. Passed to the
    runtime adapter, which calls consume_* methods at the appropriate
    points. The first consume call that exceeds a limit emits a
    BudgetExceededEvent and raises BudgetExceeded.

    The tracker holds the base event fields (workload, contract,
    trace_id, span_id, contract_version) so it can emit cleanly without
    requiring the caller to assemble them.
    """

    def __init__(
        self,
        budget: ActionBudget,
        *,
        sink: EventSink | None = None,
        base_event_fields: dict[str, Any] | None = None,
        initial_steps: int = 0,
        initial_tokens: int = 0,
        initial_tool_calls: int = 0,
        initial_per_tool: dict[str, int] | None = None,
        initial_per_tool_tokens: dict[str, int] | None = None,
        initial_per_tool_seconds: dict[str, float] | None = None,
        initial_cost_usd: float = 0.0,
    ) -> None:
        """Construct a per-run counter.

        The ``initial_*`` keyword arguments seed the counters from a
        prior run's consumed totals (BL-154); they default to zero, the
        exact L1 fresh-run behaviour. The harness passes them on a
        resume so an approval pause does not reset the budget. The
        wall-clock origin is intentionally NOT carried across a pause:
        elapsed time is measured per resumed leg, since a human approval
        can take arbitrarily long and is not workload runtime.
        """
        self._budget = budget
        self._sink: EventSink = sink if sink is not None else NullSink()
        self._base = base_event_fields if base_event_fields is not None else {}
        self._steps = initial_steps
        self._tokens = initial_tokens
        self._tool_calls = initial_tool_calls
        self._per_tool: dict[str, int] = dict(initial_per_tool or {})
        self._per_tool_tokens: dict[str, int] = dict(initial_per_tool_tokens or {})
        self._per_tool_seconds: dict[str, float] = dict(initial_per_tool_seconds or {})
        self._cost_usd = initial_cost_usd
        self._started_at = datetime.now(UTC)

    @property
    def budget(self) -> ActionBudget:
        return self._budget

    @property
    def steps(self) -> int:
        return self._steps

    @property
    def tokens(self) -> int:
        return self._tokens

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    @property
    def cost_usd(self) -> float:
        return self._cost_usd

    def snapshot(self) -> dict[str, Any]:
        """Consumed totals, for threading into a ResumableState (BL-154).

        The keys match the ``ResumableState.consumed_*`` fields so a
        caller can persist the budget across an approval pause and seed
        the resumed run's tracker from them.
        """
        return {
            "consumed_steps": self._steps,
            "consumed_tokens": self._tokens,
            "consumed_tool_calls": self._tool_calls,
            "consumed_per_tool": dict(self._per_tool),
            "consumed_per_tool_tokens": dict(self._per_tool_tokens),
            "consumed_per_tool_seconds": dict(self._per_tool_seconds),
            "consumed_cost_usd": self._cost_usd,
        }

    def consume_cost(self, usd: float) -> None:
        """Record ``usd`` spend and enforce ``max_cost_usd`` (BL-123).

        No-op when ``usd`` is zero so an adapter that cannot price a run
        (no cost signal) leaves the dimension at 0 and unbounded-in-fact.
        """
        # BL-221: caller-fed float trust boundary. NaN is truthy in
        # Python (so the `if usd:` short-circuit does NOT skip it),
        # NaN propagates through `+` (so the accumulator becomes NaN
        # for the rest of the run), and `NaN > limit` is always False
        # (so `_check` never trips). Net effect of a single NaN cost
        # report: the ceiling is silently disabled. Same class as
        # BL-159 / BL-205 (non-finite numeric coercion at a trust
        # boundary) applied to the caller-fed budget input.
        if not math.isfinite(usd):
            raise ValueError(f"consume_cost requires a finite float, got {usd!r}")
        if usd < 0:
            raise ValueError(f"consume_cost requires non-negative, got {usd!r}")
        if usd:
            self._cost_usd += usd
            self._check("cost", self._cost_usd, self._budget.max_cost_usd)

    def consume_step(self, n: int = 1) -> None:
        """Record n steps consumed and enforce the steps limit."""
        self._steps += n
        self._check("steps", float(self._steps), self._budget.max_steps)

    def consume_tokens(self, n: int) -> None:
        """Record n tokens consumed and enforce the tokens limit."""
        self._tokens += n
        self._check("tokens", float(self._tokens), self._budget.max_tokens)

    def consume_tool_call(
        self,
        n: int = 1,
        *,
        tool: str | None = None,
        tokens: int = 0,
        wall_clock_seconds: float = 0.0,
    ) -> None:
        """Record n tool calls and enforce the aggregate + per-tool caps.

        ``tool`` is the tool name. When given and a per-tool cap applies
        (BL-073), that cap is enforced after the aggregate cap. Calling
        without ``tool`` preserves the L1 behaviour (aggregate only).

        ``tokens`` and ``wall_clock_seconds`` (BL-123) attribute resource
        use to ``tool`` and enforce ``max_tokens_per_tool`` /
        ``max_wall_clock_seconds_per_tool``. Both default to 0 so an
        adapter that does not attribute per-tool resources keeps the
        exact L1/BL-073 call-count behaviour.
        """
        # BL-221: same caller-fed float trust boundary as consume_cost.
        # ``wall_clock_seconds`` is a `float` so NaN/inf are valid Python
        # values; if either reached `_check`, the per-tool wall-clock
        # ceiling would silently break (NaN > limit is False; the
        # accumulator becomes NaN/inf for the rest of the run). Validate
        # at the entry boundary so a buggy adapter or a misconfigured
        # pricing helper surfaces the bug here rather than disabling the
        # cap in production.
        if not math.isfinite(wall_clock_seconds):
            raise ValueError(
                f"consume_tool_call requires a finite wall_clock_seconds, "
                f"got {wall_clock_seconds!r}"
            )
        if wall_clock_seconds < 0:
            raise ValueError(
                f"consume_tool_call requires non-negative wall_clock_seconds, "
                f"got {wall_clock_seconds!r}"
            )
        self._tool_calls += n
        self._check("tool_calls", float(self._tool_calls), self._budget.max_tool_calls)
        if tool is not None:
            self._per_tool[tool] = self._per_tool.get(tool, 0) + n
            per_tool = self._budget.max_tool_calls_per_tool or {}
            if tool in per_tool:
                self._check(
                    f"tool_calls:{tool}",
                    float(self._per_tool[tool]),
                    per_tool[tool],
                )
            if tokens:
                self._per_tool_tokens[tool] = self._per_tool_tokens.get(tool, 0) + tokens
                tok_caps = self._budget.max_tokens_per_tool or {}
                if tool in tok_caps:
                    self._check(
                        f"tokens:{tool}",
                        float(self._per_tool_tokens[tool]),
                        tok_caps[tool],
                    )
            if wall_clock_seconds:
                self._per_tool_seconds[tool] = (
                    self._per_tool_seconds.get(tool, 0.0) + wall_clock_seconds
                )
                sec_caps = self._budget.max_wall_clock_seconds_per_tool or {}
                if tool in sec_caps:
                    self._check(
                        f"wall_clock:{tool}",
                        self._per_tool_seconds[tool],
                        sec_caps[tool],
                    )

    def check_wall_clock(self) -> None:
        """Check elapsed wall-clock time against max_wall_clock_seconds."""
        elapsed = (datetime.now(UTC) - self._started_at).total_seconds()
        self._check("wall_clock", elapsed, self._budget.max_wall_clock_seconds)

    def emit_wall_clock_exceeded(self, elapsed: float) -> None:
        """Emit a `BudgetExceededEvent` for the wall_clock kind (`BL-202`).

        Used by the runtime's end-to-end-deadline fallback path: when
        the outer loop or the watchdog raises `BudgetExceeded` at the
        exact boundary instant where the tracker's strict `>` check
        does not trip, the bare raise was unpaired in the EventSink
        stream (BL-167 + BL-189 class extension: audit-vs-raise
        parity). This method emits the event so every wall-clock
        terminal raise has a matching event in the audit stream.
        """
        limit = self._budget.max_wall_clock_seconds
        if limit is None or not self._base:
            return
        self._sink.emit(
            BudgetExceededEvent(
                timestamp=datetime.now(UTC),
                budget_kind="wall_clock",
                limit=float(limit),
                consumed=elapsed,
                **self._base,
            )
        )

    def remaining(self, kind: BudgetKind) -> float:
        """Return remaining budget for the given kind.

        Returns float('inf') if no limit is set for that kind.
        """
        if kind == "steps":
            return _remaining(self._steps, self._budget.max_steps)
        if kind == "tokens":
            return _remaining(self._tokens, self._budget.max_tokens)
        if kind == "tool_calls":
            return _remaining(self._tool_calls, self._budget.max_tool_calls)
        if kind == "cost":
            return _remaining(self._cost_usd, self._budget.max_cost_usd)
        elapsed = (datetime.now(UTC) - self._started_at).total_seconds()
        return _remaining(elapsed, self._budget.max_wall_clock_seconds)

    def _check(self, kind: str, consumed: float, limit: float | int | None) -> None:
        if limit is None:
            return
        if consumed > float(limit):
            if self._base:
                self._sink.emit(
                    BudgetExceededEvent(
                        timestamp=datetime.now(UTC),
                        budget_kind=kind,
                        limit=float(limit),
                        consumed=consumed,
                        **self._base,
                    )
                )
            raise BudgetExceeded(kind, float(limit), consumed)


def _remaining(consumed: float, limit: float | int | None) -> float:
    if limit is None:
        return float("inf")
    return max(0.0, float(limit) - consumed)
