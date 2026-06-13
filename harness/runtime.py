"""Runtime adapter protocol and the default PydanticAI adapter.

The runtime adapter abstracts the agent framework powering a workload.
The harness contract is:

- Workloads receive a Runtime instance, not a framework type.
- Swapping runtimes (e.g. PydanticAI -> LangGraph -> Smolagents) is a
  Protocol-conformance check, not a workload rewrite.
- Sandboxing, action budgets, tool-use authorization, and observability
  are enforced by the harness around the Runtime, not delegated to it.

Default adapter: PydanticAIRuntime. See docs/adr/0001-runtime-selection.md.

Phase 2 added budget, mcp_servers, and guard parameters
(docs/adr/0003-budgets-mcp-guards.md). L2 (docs/adr/0007) adds the
optional ``resume`` parameter and wires the previously-deferred runtime
behaviour into PydanticAIRuntime:

- BL-001: every tool call passes ``guard.check`` before execution and
  honours APPROVE / REJECT (hard raises, soft logs-and-continues) /
  REQUIRE_APPROVAL.
- BL-002: REQUIRE_APPROVAL pauses the run and surfaces a ResumableState;
  passing it back via ``resume`` continues from the decision.
- BL-003: an asyncio.wait_for watchdog enforces wall-clock without
  needing explicit checkpoints. It preempts at the next await/IO
  boundary; a fully blocking, non-cooperative tool that never yields
  is still not killed (ADR 0003; LIMITATIONS L11).
- BL-004: streaming accumulates token usage and raises BudgetExceeded
  as soon as a limit is crossed.
- BL-073: per-tool call quotas via the BudgetTracker.
- BL-132/BL-171: opt-in ``model_settings`` pass-through (the surface
  for provider-side prompt-cache breakpoints) and cache hit/creation
  token surfacing via ``BudgetTracker.consume_cache_tokens``.
- BL-114: opt-in ``approval_mode="deferred"`` rebuilds the approval
  pause/resume on PydanticAI's DeferredToolRequests /
  DeferredToolResults: the paused leg's message history travels in
  ``ResumableState.runtime_state`` and the resumed leg continues from
  it instead of replaying the run (ADR 0027).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import math
import time
import uuid
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from harness.authority import AuthorityTier
from harness.budgets import BudgetTracker
from harness.contract import Severity
from harness.errors import ApprovalDenied as ApprovalDeniedError
from harness.errors import BudgetExceeded, GovernanceViolation, HarnessError
from harness.evidence import EvidenceContext, EvidenceHook
from harness.guard import GuardDecision, ToolGuard
from harness.interruption import ApprovalInterruption, ResumableState
from harness.mcp import MCPServerSpec, MCPTransport

__all__ = ["PydanticAIRuntime", "RetryPolicy", "Runtime"]


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry with exponential backoff for the runtime call (BL-136).

    Opt-in: the default ``PydanticAIRuntime`` has no policy and behaves
    exactly as before (one attempt). A policy only retries an exception
    whose type is in ``retry_on`` (a transient framework/provider error
    the caller classifies). Contract-terminal outcomes are never
    retried: GovernanceViolation, ApprovalDenied, BudgetExceeded, the
    internal approval pause, and cancellation propagate on the first
    occurrence regardless of ``retry_on``.

    Budget interaction across attempts (the tracker is constructed once
    by the harness and shared by every attempt):

    - Wall-clock is bounded end to end: ``run`` derives each attempt's
      remaining timeout from the original deadline and counts backoff
      against it, so retries cannot turn the cap into a per-attempt
      allowance.
    - Tool-call / per-tool quotas are fed live from the gate as each
      call is proposed, so a failed attempt's tool calls still count.
    - Token and step usage is charged from the *final* attempt's
      ``result.usage`` once the run succeeds. PydanticAI raises without
      exposing partial usage on a failed ``agent.run()``, so a failed
      attempt's model round-trips are not counted against
      ``max_tokens`` / ``max_steps``. A retried run can therefore exceed
      those two dimensions by the failed legs' usage; bound a retrying
      run with the wall-clock or tool-call dimension if a hard token
      ceiling matters. Closing this needs upstream partial-usage on the
      exception path (tracked, ``LIMITATIONS.md``).

    ``circuit_breaker_threshold`` trips a per-instance breaker after that
    many consecutive fully-failed calls (a call that exhausted its
    retries or failed with retries disabled): while tripped a call makes
    a single attempt with no backoff loop, and the breaker resets on the
    first success.
    """

    max_retries: int = 0
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 30.0
    retry_on: tuple[type[BaseException], ...] = ()
    circuit_breaker_threshold: int | None = None

    def __post_init__(self) -> None:
        """Reject non-finite / negative policy parameters (BL-231).

        The config-side dual of BL-221. A non-finite
        ``backoff_base_seconds`` / ``backoff_max_seconds`` makes
        ``delay_for`` non-finite, and ``asyncio.sleep(NaN)`` returns
        immediately (``min(NaN, deadline)`` keeps the NaN), so a NaN
        backoff turns the bounded exponential backoff this policy
        promises into a no-delay retry storm against the failing
        provider. A negative ``max_retries`` or a
        ``circuit_breaker_threshold < 1`` is a meaningless spec.
        Validated at construction (ADR 0007), mirroring the
        ``Namespace`` (BL-197) and ``MultiDispatcher`` (BL-205) guards.
        """
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be non-negative (got {self.max_retries!r})")
        for field_name, value in (
            ("backoff_base_seconds", self.backoff_base_seconds),
            ("backoff_max_seconds", self.backoff_max_seconds),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite (got {value!r})")
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative (got {value!r})")
        if self.circuit_breaker_threshold is not None and self.circuit_breaker_threshold < 1:
            raise ValueError(
                f"circuit_breaker_threshold must be >= 1 or None "
                f"(got {self.circuit_breaker_threshold!r})"
            )

    def delay_for(self, attempt: int) -> float:
        """Backoff before retry ``attempt`` (1-based): base * 2**(n-1)."""
        return min(self.backoff_base_seconds * (2.0 ** (attempt - 1)), self.backoff_max_seconds)


@runtime_checkable
class Runtime(Protocol):
    """Adapter contract between a workload and the underlying agent framework.

    Implementations must be async-safe and side-effect-free at construction.
    All run-scoped state is held by the harness, not the runtime instance.
    """

    name: str

    async def run(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
        resume: ResumableState | None = None,
    ) -> Any:
        """Execute a single agent run to completion.

        Args:
            prompt: User prompt or task description.
            tools: Locally-defined tool definitions for this run.
            deps: Dependency object injected into tool calls.
            budget: Action budget tracker. Adapters must call
                consume_step / consume_tokens / consume_tool_call /
                check_wall_clock at appropriate points so the tracker
                can enforce limits and emit BudgetExceededEvent.
            mcp_servers: MCP server specs the adapter should start before
                the run and stop after. Tools exposed by these servers
                are merged with the `tools` list.
            guard: Tool guard invoked before each proposed tool call.
                Adapters must respect GuardDecision (APPROVE / REJECT /
                REQUIRE_APPROVAL). On REJECT with HARD severity, raise
                GovernanceViolation. On REQUIRE_APPROVAL, capture the
                proposed action and surface it through the harness's
                ResumableState mechanism.
            resume: A ResumableState whose approvals have been resolved,
                continuing a run previously paused by REQUIRE_APPROVAL.
                None for a fresh run.

        Returns:
            Framework-specific result, or a ResumableState if the run
            paused on an approval. The harness validates a non-resumable
            result against the workload's declared output schema before
            exposing it upstream.
        """
        ...

    def stream(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
        resume: ResumableState | None = None,
    ) -> AsyncIterator[Any]:
        """Stream incremental output from an agent run.

        Same parameter semantics as run(). Streaming events are
        framework-specific. The harness normalizes them to OTel GenAI
        semantic conventions before forwarding.
        """
        ...


class _ApprovalPause(Exception):
    """Internal: a tool requires approval and no decision is available."""

    def __init__(self, interruption: ApprovalInterruption) -> None:
        super().__init__(f"approval required for tool {interruption.tool!r}")
        self.interruption = interruption


class _GuardState:
    """Run-scoped box shared with wrapped tools.

    PydanticAI may wrap or reshape an exception raised inside a tool, so
    the wrapper records the terminal decision here and the adapter reads
    it back deterministically after the run unwinds.
    """

    def __init__(self, *, soft_reject_as_error: bool = False) -> None:
        self.pause: ApprovalInterruption | None = None
        self.governance: GovernanceViolation | None = None
        self.denied: ApprovalDeniedError | None = None
        # BL-251: per-call approval context (tier, rollback_plan) recorded
        # by the deferred gate keyed by tool_call_id, so the deferred
        # pause state can carry it onto each ApprovalInterruption the same
        # way the replay path reads it straight off the GuardResponse.
        self.approval_context: dict[str, tuple[AuthorityTier | None, str | None]] = {}
        # BL-137: when set, a soft governance reject is raised as a
        # framework tool-retry error (a typed rejection the model sees
        # as an error) instead of returned as the tool's string value.
        self.soft_reject_as_error = soft_reject_as_error


def _usage(result: Any) -> Any:
    """Read PydanticAI run usage.

    ``usage`` has been a property since PydanticAI 1.97 (the legacy
    ``usage()`` call form is deprecated and warns), so access the
    property directly. Deliberately version-agnostic: Renovate moves
    the locked version, and the property access is the stable form
    across the supported range.
    """
    return result.usage


def _surface_cache_tokens(budget: BudgetTracker, usage: Any) -> None:
    """Surface prompt-cache token counts into the tracker (BL-132).

    getattr-guarded: a usage object without the cache fields (an older
    PydanticAI, a custom Model double) surfaces nothing, the same
    compat stance as ``_usage``. ``or 0`` clamps an explicit ``None``.
    Zero counts are skipped so an uncached run leaves the tracker
    untouched.
    """
    read = getattr(usage, "cache_read_tokens", 0) or 0
    write = getattr(usage, "cache_write_tokens", 0) or 0
    if read or write:
        budget.consume_cache_tokens(read=read, write=write)


def _tool_name(tool: Any) -> str:
    """Best-effort stable name for a tool (callable or pydantic_ai Tool)."""
    for attr in ("name", "__name__"):
        value = getattr(tool, attr, None)
        if isinstance(value, str) and value:
            return value
    func = getattr(tool, "function", None)
    if func is not None:
        return _tool_name(func)
    return repr(tool)


def _resolved_decision(
    resume: ResumableState | None,
    tool: str,
    arguments: dict[str, Any],
    used: set[str],
) -> ApprovalInterruption | None:
    """First not-yet-consumed resolved approval matching this proposal.

    Binding is by the (tool, arguments) tuple: a stale approval for the
    same tool with different arguments must not satisfy a new call. The
    default ``HarnessToolGuard`` mints a fresh ``interruption_id`` for
    every check, so the id is not a stable cross-pause handle and cannot
    be used as the binding key on its own.
    """
    if resume is None:
        return None
    for ai in resume.pending_approvals:
        if (
            ai.tool == tool
            and ai.arguments == arguments
            and ai.id not in used
            and ai.decision != "pending"
        ):
            used.add(ai.id)
            return ai
    return None


def _restate_satisfied(ai: ApprovalInterruption, arguments: dict[str, Any]) -> bool:
    """Whether a resolved approval clears the two-step restate gate (BL-252).

    An irreversible (Tier 3) approval is honoured only when the human
    re-entered the arguments and they match the proposed call, the
    two-step confirmation (ADR 0033). This composes with the BL-193
    (tool, arguments) binding ``_resolved_decision`` already checked: the
    restatement must equal the live call arguments, so a stale or
    mis-typed restatement does not authorise execution. Lower tiers need
    no restatement, so the gate is vacuously satisfied.
    """
    if ai.tier != AuthorityTier.IRREVERSIBLE:
        return True
    return ai.restated_arguments == arguments


def _rejection(response: Any, name: str, state: _GuardState) -> str:
    """Translate a REJECT guard decision (shared by both gate modes).

    Raises GovernanceViolation on HARD; on SOFT returns the L1
    ``[blocked: ...]`` string, or raises the framework's typed
    ModelRetry when ``soft_reject_as_error`` is set (BL-137).
    """
    if response.severity == Severity.HARD:
        state.governance = GovernanceViolation(response.reason or "governance", name)
        raise state.governance
    reason = response.reason or "governance predicate failed"
    if state.soft_reject_as_error:
        # BL-137: surface a typed rejection the model handles as
        # a tool error, not apparent tool output. ModelRetry is
        # PydanticAI's structured tool-error channel; fall back
        # to the L1 string if the symbol is unavailable.
        try:
            from pydantic_ai import ModelRetry
        except ImportError:  # pragma: no cover - env dependent
            return f"[blocked: {reason}]"
        raise ModelRetry(f"blocked by governance: {reason}")
    return f"[blocked: {reason}]"


@dataclass(frozen=True)
class _GateResult:
    """Outcome of one gate check (shared by both gate modes).

    ``soft`` is a soft-reject message to surface to the model (the L1
    ``[blocked: ...]`` string or a workload variant); ``soft is None``
    means proceed. On the proceed path the action's ``tier`` and
    ``rollback_plan`` (read off the GuardResponse) ride along so the tool
    wrapper can drive the BL-253 evidence hook without re-querying the
    guard. Both are None when no guard, or no classifier / planner, is
    configured.
    """

    soft: str | None = None
    tier: AuthorityTier | None = None
    rollback_plan: str | None = None


async def _with_evidence(
    hook: EvidenceHook | None,
    gate: _GateResult,
    *,
    tool: str,
    arguments: dict[str, Any],
    tool_call_id: str | None,
    run: Callable[[], Awaitable[Any]],
) -> Any:
    """Bracket an approved tool body in the evidence hook (BL-253).

    ``run`` is the path's own already-shaped body coroutine factory (the
    sync / async / await semantics live there). For anything but an
    ``IRREVERSIBLE`` (Tier 3) action with a hook configured this is just
    ``await run()``, the prior path byte-for-byte. Otherwise ``before``
    runs first, then the body, then ``after`` in a ``finally`` with the
    body's exception (``None`` on success), so a Tier 3 action that
    raised is still recorded. The token ``before`` returns is handed to
    ``after`` so concurrent Tier 3 bodies pair without shared state. A
    ``before`` that itself raises aborts the action before ``run`` is
    called and ``after`` is not invoked (BL-261, fifteenth audit:
    fail-safe, no completed action to record), so a hook's ``before``
    should capture pre-state atomically.

    Tier 3 always routes through approval first (Tier 3 >= the STATEFUL
    default threshold), so this fires on the post-approval leg, never on
    a first-pass APPROVE; in deferred mode the resumed leg runs the body
    once, so it fires once. The bracket sits inside the wrapper's
    per-tool wall-clock window, so a hook's own duration counts toward
    ``max_wall_clock_seconds_per_tool`` (keep a hook light, or raise that
    cap); the run-level wall-clock watchdog bounds it regardless.
    """
    tier = gate.tier
    if hook is None or tier is not AuthorityTier.IRREVERSIBLE:
        return await run()
    context = EvidenceContext(
        # Shallow-copy the arguments so the captured context is a stable
        # snapshot: the live dict is also handed to the tool body (the MCP
        # path passes it to call_tool), and a hook may keep the context as
        # its token, so it must not observe a later mutation (BL-253).
        tool=tool,
        arguments=dict(arguments),
        tier=tier,
        tool_call_id=tool_call_id,
        rollback_plan=gate.rollback_plan,
    )
    token = await hook.before(context)
    error: BaseException | None = None
    try:
        return await run()
    except BaseException as exc:
        error = exc
        raise
    finally:
        await hook.after(token, error=error)


async def _gate(
    name: str,
    arguments: dict[str, Any],
    *,
    guard: ToolGuard | None,
    budget: BudgetTracker | None,
    resume: ResumableState | None,
    state: _GuardState,
    used_approvals: set[str],
) -> _GateResult:
    """Guard + budget gate for one proposed tool call.

    Returns a `_GateResult`: ``soft`` set is a soft-reject message to
    surface to the model; ``soft is None`` is clearance to proceed,
    carrying the action's tier / rollback plan for the BL-253 evidence
    hook. Raises GovernanceViolation (hard reject), _ApprovalPause
    (approval needed, no decision yet), or ApprovalDenied. Used for both
    locally-defined tools and MCP-exposed tools so neither bypasses
    governance / budget (BL-001/073).
    """
    tier: AuthorityTier | None = None
    rollback_plan: str | None = None
    if guard is not None:
        response = await guard.check(name, arguments)
        if response.decision == GuardDecision.REJECT:
            return _GateResult(soft=_rejection(response, name, state))
        if response.decision == GuardDecision.REQUIRE_APPROVAL:
            decided = _resolved_decision(resume, name, arguments, used_approvals)
            if decided is not None and decided.decision == "denied":
                state.denied = ApprovalDeniedError(name, decided.decision_reason)
                raise state.denied
            # A missing decision, or an irreversible (Tier 3) approval
            # whose restated arguments do not match, re-pauses for a
            # (fresh) decision (BL-252, the two-step confirmation).
            if decided is None or not _restate_satisfied(decided, arguments):
                interruption = ApprovalInterruption(
                    id=response.interruption_id or uuid.uuid4().hex,
                    created_at=datetime.now(UTC),
                    tool=name,
                    arguments=arguments,
                    tier=response.tier,
                    rollback_plan=response.rollback_plan,
                )
                state.pause = interruption
                raise _ApprovalPause(interruption)
        # Proceed: carry the tier / rollback plan (set on APPROVE and on
        # the approved REQUIRE_APPROVAL response) to the evidence hook.
        tier = response.tier
        rollback_plan = response.rollback_plan
    if budget is not None:
        budget.consume_tool_call(tool=name)
    return _GateResult(tier=tier, rollback_plan=rollback_plan)


async def _deferred_gate(
    name: str,
    arguments: dict[str, Any],
    ctx: Any,
    *,
    guard: ToolGuard | None,
    budget: BudgetTracker | None,
    resume: ResumableState | None,
    state: _GuardState,
    used_approvals: set[str],
) -> _GateResult:
    """Deferred-mode guard + budget gate for one proposed tool call (BL-114).

    The deferred twin of ``_gate``: the REJECT branches are identical
    (shared ``_rejection``), but REQUIRE_APPROVAL raises PydanticAI's
    ``ApprovalRequired`` instead of pausing the whole run, so the
    framework collects every needed approval and ends the leg with a
    DeferredToolRequests output. On a resumed leg the framework
    re-invokes the call with ``ctx.tool_call_approved`` set; the
    recorded approval is then verified by the full (tool, arguments)
    tuple and consumed (the BL-193 binding, defence in depth over the
    upstream tool_call_id mapping), so an approval recorded for
    different arguments, an approval already consumed by a retried
    leg, or a tampered state re-pauses for a fresh decision instead of
    executing. A denial never reaches this gate: the caller's
    ``ToolDenied`` is turned into a model-visible tool error by the
    framework and the run continues (the deliberate semantic
    divergence from replay mode's terminal ApprovalDenied; ADR 0027).
    """
    tier: AuthorityTier | None = None
    rollback_plan: str | None = None
    if guard is not None:
        response = await guard.check(name, arguments)
        if response.decision == GuardDecision.REJECT:
            return _GateResult(soft=_rejection(response, name, state))
        if response.decision == GuardDecision.REQUIRE_APPROVAL:
            from pydantic_ai.exceptions import ApprovalRequired

            # BL-251: record the approval context keyed by this call's
            # tool_call_id (the stable id the pause state will use), so
            # the deferred pause carries the tier / rollback plan onto its
            # ApprovalInterruption symmetrically with the replay path.
            call_id = getattr(ctx, "tool_call_id", None)
            if call_id is not None:
                state.approval_context[call_id] = (response.tier, response.rollback_plan)
            if not bool(getattr(ctx, "tool_call_approved", False)):
                raise ApprovalRequired
            decided = _resolved_decision(resume, name, arguments, used_approvals)
            # A Tier 3 approval without a matching restatement re-pauses,
            # the same two-step gate as the replay path (BL-252).
            if (
                decided is None
                or decided.decision != "approved"
                or not _restate_satisfied(decided, arguments)
            ):
                raise ApprovalRequired
        # Proceed: carry the tier / rollback plan to the evidence hook,
        # the deferred twin of the replay gate's proceed path (BL-253).
        tier = response.tier
        rollback_plan = response.rollback_plan
    if budget is not None:
        budget.consume_tool_call(tool=name)
    return _GateResult(tier=tier, rollback_plan=rollback_plan)


def _wrap_tool(
    tool: Any,
    *,
    guard: ToolGuard | None,
    budget: BudgetTracker | None,
    resume: ResumableState | None,
    state: _GuardState,
    used_approvals: set[str],
    evidence_hook: EvidenceHook | None = None,
) -> Any:
    """Wrap a tool so the guard and budget run before its body.

    The wrapper preserves the original signature and annotations so
    PydanticAI still infers the correct tool JSON schema. A configured
    ``evidence_hook`` brackets the body for an irreversible (Tier 3)
    action (BL-253); every other call is unchanged.
    """
    func = getattr(tool, "function", tool)
    name = _tool_name(tool)
    is_async = inspect.iscoroutinefunction(func)

    async def _local_gate(arguments: dict[str, Any]) -> _GateResult:
        return await _gate(
            name,
            arguments,
            guard=guard,
            budget=budget,
            resume=resume,
            state=state,
            used_approvals=used_approvals,
        )

    def _charge_wall_clock(started: float) -> None:
        # The gate already counted the call (pre-execution, so an
        # over-quota call is blocked before it runs); attribute the
        # measured body duration to the per-tool wall-clock cap here,
        # post-execution (n=0 adds no count). This is what makes
        # ActionBudget.max_wall_clock_seconds_per_tool fire in a real
        # run. Per-tool token attribution is intentionally NOT done
        # here: a tool call does not itself consume model tokens (the
        # model round-trips do) and PydanticAI reports usage at the run
        # level, so the default adapter has no per-tool token signal;
        # max_tokens_per_tool is a caller-fed surface (a tool that
        # itself calls a model can pass tokens=...).
        if budget is not None:
            budget.consume_tool_call(0, tool=name, wall_clock_seconds=time.perf_counter() - started)

    @functools.wraps(func)
    async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
        gate = await _local_gate(kwargs)
        if gate.soft is not None:
            return gate.soft
        started = time.perf_counter()

        async def _run() -> Any:
            result = func(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result

        try:
            return await _with_evidence(
                evidence_hook, gate, tool=name, arguments=kwargs, tool_call_id=None, run=_run
            )
        finally:
            _charge_wall_clock(started)

    @functools.wraps(func)
    async def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        gate = await _local_gate(kwargs)
        if gate.soft is not None:
            return gate.soft
        started = time.perf_counter()

        async def _run() -> Any:
            return func(*args, **kwargs)

        try:
            return await _with_evidence(
                evidence_hook, gate, tool=name, arguments=kwargs, tool_call_id=None, run=_run
            )
        finally:
            _charge_wall_clock(started)

    wrapper = _async_wrapper if is_async else _sync_wrapper
    with contextlib.suppress(ValueError, TypeError):
        wrapper.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
    wrapper.__annotations__ = dict(getattr(func, "__annotations__", {}))
    return wrapper


def _wrap_tool_deferred(
    tool: Any,
    *,
    guard: ToolGuard | None,
    budget: BudgetTracker | None,
    resume: ResumableState | None,
    state: _GuardState,
    used_approvals: set[str],
    evidence_hook: EvidenceHook | None = None,
) -> Any:
    """Deferred-mode tool wrapper (BL-114): ``_wrap_tool`` with a context.

    Prepends a ``RunContext`` parameter so the gate can read
    ``ctx.tool_call_approved`` on a resumed leg; PydanticAI excludes
    RunContext parameters from the inferred JSON schema, so the
    model-visible signature is unchanged. A tool whose own first
    parameter is a RunContext receives it pass-through instead of a
    second one.
    """
    from pydantic_ai import RunContext

    func = getattr(tool, "function", tool)
    name = _tool_name(tool)
    orig_params = list(inspect.signature(func).parameters.values())
    orig_takes_ctx = bool(orig_params) and "RunContext" in str(orig_params[0].annotation)

    async def _wrapper(ctx: Any, **kwargs: Any) -> Any:
        gate = await _deferred_gate(
            name,
            kwargs,
            ctx,
            guard=guard,
            budget=budget,
            resume=resume,
            state=state,
            used_approvals=used_approvals,
        )
        if gate.soft is not None:
            return gate.soft
        started = time.perf_counter()

        async def _run() -> Any:
            result = func(ctx, **kwargs) if orig_takes_ctx else func(**kwargs)
            return await result if inspect.isawaitable(result) else result

        try:
            return await _with_evidence(
                evidence_hook,
                gate,
                tool=name,
                arguments=kwargs,
                tool_call_id=getattr(ctx, "tool_call_id", None),
                run=_run,
            )
        finally:
            # Per-tool wall-clock parity with _wrap_tool; the gate
            # already counted the call (n=0 adds no count).
            if budget is not None:
                budget.consume_tool_call(
                    0, tool=name, wall_clock_seconds=time.perf_counter() - started
                )

    params = [
        inspect.Parameter(
            "ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=RunContext[Any]
        )
    ]
    params += orig_params[1:] if orig_takes_ctx else orig_params
    with contextlib.suppress(ValueError, TypeError):
        _wrapper.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    annotations = dict(getattr(func, "__annotations__", {}))
    annotations["ctx"] = RunContext[Any]
    _wrapper.__annotations__ = annotations
    _wrapper.__name__ = getattr(func, "__name__", name)
    _wrapper.__doc__ = getattr(func, "__doc__", None)
    return _wrapper


def _deferred_resume_inputs(resume: ResumableState) -> tuple[Any, Any]:
    """Rebuild (message_history, DeferredToolResults) from a paused state.

    Fails loud (HarnessError) when the state was not produced by a
    deferred-mode run (a replay-mode state cannot be continued without
    replaying) or when any pending approval is still undecided: the
    upstream requires a result for every deferred call, so a partial
    decision set cannot be expressed as a continuation.
    """
    from pydantic_ai import DeferredToolResults
    from pydantic_ai.messages import ModelMessagesTypeAdapter
    from pydantic_ai.tools import ToolDenied

    rs = resume.runtime_state
    if not rs or rs.get("mode") != "deferred" or "messages" not in rs:
        raise HarnessError(
            "approval_mode='deferred' requires a ResumableState produced by a "
            "deferred-mode run; this state carries no deferred runtime_state "
            "(a replay-mode pause cannot be resumed without replaying)"
        )
    undecided = [ai.id for ai in resume.pending_approvals if ai.decision == "pending"]
    if undecided:
        raise HarnessError(
            "deferred resume requires a decision for every pending approval; "
            f"undecided: {undecided}"
        )
    approvals: dict[str, Any] = {}
    for ai in resume.pending_approvals:
        if ai.decision == "approved":
            approvals[ai.id] = True
        else:
            approvals[ai.id] = ToolDenied(message=ai.decision_reason or "denied by operator")
    history = ModelMessagesTypeAdapter.validate_python(rs["messages"])
    return history, DeferredToolResults(approvals=approvals)


def _to_pydantic_mcp(spec: MCPServerSpec, process_tool_call: Any = None) -> Any:
    """Translate an MCPServerSpec into a PydanticAI MCP toolset.

    Honours the spec's ``headers`` (HTTP/SSE auth) and ``allowed_tools``
    allowlist -- both are validated manifest surface; dropping them
    would silently bypass workload-declared MCP restrictions.
    ``process_tool_call`` routes every MCP tool invocation through the
    harness guard + budget gate so MCP tools cannot bypass governance
    or budgets (BL-001/073).
    """
    from pydantic_ai.mcp import MCPServerSSE, MCPServerStdio, MCPServerStreamableHTTP

    server: Any
    # 1.97 deprecates these explicit classes in favour of MCPToolset(...)
    # but keeps them functional through the pinned range; the Protocol
    # boundary (ADR 0007) absorbs this churn, so silence the local
    # DeprecationWarning rather than chase a pre-v2 API rename.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if spec.transport == MCPTransport.STDIO:
            server = MCPServerStdio(
                command=spec.command or "",
                args=list(spec.args),
                timeout=spec.timeout_seconds,
                process_tool_call=process_tool_call,
            )
        elif spec.transport == MCPTransport.SSE:
            server = MCPServerSSE(
                url=spec.url or "",
                headers=dict(spec.headers),
                timeout=spec.timeout_seconds,
                process_tool_call=process_tool_call,
            )
        else:
            server = MCPServerStreamableHTTP(
                url=spec.url or "",
                headers=dict(spec.headers),
                timeout=spec.timeout_seconds,
                process_tool_call=process_tool_call,
            )

    if spec.allowed_tools is not None:
        allowed = set(spec.allowed_tools)
        server = server.filtered(lambda ctx, tool_def: tool_def.name in allowed)
    return server


class PydanticAIRuntime:
    """Default runtime adapter backed by PydanticAI.

    ``model`` is anything PydanticAI's Agent accepts: a provider string
    ("anthropic:claude-opus-4-7") or a model instance (TestModel /
    FunctionModel for deterministic, network-free use). ``output_type``
    is the PydanticAI structured-output type; the harness still
    re-validates against the workload's declared schema.

    ``model_settings`` (BL-132/BL-171) is forwarded verbatim to the
    underlying Agent; ``None`` preserves the prior behaviour. It is the
    opt-in surface for provider-side controls, in particular Anthropic
    prompt caching: pass ``AnthropicModelSettings(
    anthropic_cache_instructions=True,
    anthropic_cache_tool_definitions=True)`` (or the equivalent plain
    dict) to pin cache breakpoints on the stable system/tools prefix.
    The adapter treats the value as opaque, exactly like ``model``, so
    the harness stays vendor-neutral (ADR 0001). Cache hit/creation
    token counts the provider reports are surfaced through
    ``BudgetTracker.consume_cache_tokens`` (readable as
    ``tracker.cache_read_tokens`` / ``cache_write_tokens``); they are
    not charged to ``max_tokens`` (upstream reports them outside
    ``input_tokens``), and a pricing-aware caller pairs them with
    ``consume_cost`` (BL-123). Whether the provider actually serves a
    cache hit is observable only against a live API; that validation
    is coupled to the BL-120 live-workload gate (ADR 0026).

    ``approval_mode`` (BL-114, ADR 0027) selects how a
    REQUIRE_APPROVAL guard decision pauses and resumes:

    - ``"replay"`` (default): the L1/L2 behaviour, byte-identical. The
      run aborts into a ResumableState; resuming re-runs the agent
      from the original prompt and the recorded decision is matched
      when the model re-proposes the same (tool, arguments) call
      (BL-193). Earlier tool calls re-execute (LIMITATIONS L10).
    - ``"deferred"``: the pause rides PydanticAI's
      DeferredToolRequests: the leg finishes collecting every needed
      approval, the message history travels in
      ``ResumableState.runtime_state``, and the resumed leg continues
      from it, so prior tool calls are NOT re-executed and only the
      continuation is charged. Semantic divergences, deliberate and
      documented: a denial becomes a model-visible tool error
      (``ToolDenied``) and the run continues instead of raising
      ApprovalDenied; the paused leg's own usage IS charged to the
      budget at the pause boundary (the leg ran); resuming requires a
      decision for every pending approval. Wall-clock stays per leg
      and BL-154 budget seeding stays caller-driven, as in replay.
      ``stream()`` always gates in replay mode (a generator cannot
      surface a ResumableState); approval-gated tools still need
      ``run()``.

    ``evidence_hook`` (BL-253, ADR 0038) is an optional ``EvidenceHook``
    the tool wrappers invoke around an approved irreversible (Tier 3)
    action's body: ``before`` immediately before it runs and ``after``
    immediately after (in a ``finally``, with the body's exception or
    ``None``), so the audit trail records the pre/post state of a
    high-blast change. It fires only for an ``IRREVERSIBLE`` action
    (which always routes through approval first) and only when
    configured; ``None`` (the default) preserves L1 exactly. It
    captures, it does not gate (the Tier 3 approval and the BL-252
    restatement already did) nor roll back (that is the
    ``RollbackPlanner``'s descriptive plan). Applied identically across
    the replay, deferred, and MCP tool paths.
    """

    name: str = "pydantic-ai"

    def __init__(
        self,
        model: Any,
        *,
        output_type: Any = str,
        instructions: str | None = None,
        retry_policy: RetryPolicy | None = None,
        soft_reject_as_error: bool = False,
        model_settings: Any | None = None,
        approval_mode: str = "replay",
        evidence_hook: EvidenceHook | None = None,
    ) -> None:
        if approval_mode not in ("replay", "deferred"):
            # Load-time validation (ADR 0007): a typo'd mode must not
            # silently behave as replay.
            raise ValueError(
                f"approval_mode must be 'replay' or 'deferred' (got {approval_mode!r})"
            )
        self.model = model
        self._output_type = output_type
        self._instructions = instructions
        self._retry_policy = retry_policy
        self._soft_reject_as_error = soft_reject_as_error
        self._model_settings = model_settings
        self._approval_mode = approval_mode
        # BL-253: an optional workload-supplied evidence hook. When set,
        # the tool wrappers bracket an approved irreversible (Tier 3)
        # action's body in before()/after(); None preserves L1 exactly.
        self._evidence_hook = evidence_hook
        self._consecutive_failures = 0

    def _build_agent(
        self,
        tools: list[Any] | None,
        mcp_servers: list[MCPServerSpec] | None,
        *,
        guard: ToolGuard | None,
        budget: BudgetTracker | None,
        resume: ResumableState | None,
        state: _GuardState,
        used_approvals: set[str],
        deferred: bool = False,
    ) -> Any:
        from pydantic_ai import Agent

        wrap = _wrap_tool_deferred if deferred else _wrap_tool
        wrapped = [
            wrap(
                t,
                guard=guard,
                budget=budget,
                resume=resume,
                state=state,
                used_approvals=used_approvals,
                evidence_hook=self._evidence_hook,
            )
            for t in (tools or [])
        ]

        async def _mcp_process(
            ctx: Any, call_tool: Any, name: str, tool_args: dict[str, Any]
        ) -> Any:
            # Same guard + budget gate as local tools, so MCP tool calls
            # cannot bypass governance/approval/budget (BL-001/073). In
            # deferred mode the gate raises ApprovalRequired through the
            # toolset call path, the same collection mechanism as local
            # tools (BL-114).
            if deferred:
                gate = await _deferred_gate(
                    name,
                    tool_args,
                    ctx,
                    guard=guard,
                    budget=budget,
                    resume=resume,
                    state=state,
                    used_approvals=used_approvals,
                )
            else:
                gate = await _gate(
                    name,
                    tool_args,
                    guard=guard,
                    budget=budget,
                    resume=resume,
                    state=state,
                    used_approvals=used_approvals,
                )
            if gate.soft is not None:
                return gate.soft
            started = time.perf_counter()

            async def _run() -> Any:
                return await call_tool(name, tool_args)

            try:
                return await _with_evidence(
                    self._evidence_hook,
                    gate,
                    tool=name,
                    arguments=tool_args,
                    tool_call_id=getattr(ctx, "tool_call_id", None),
                    run=_run,
                )
            finally:
                # Per-tool wall-clock parity with local tools (BL-123);
                # the gate already counted the call. n=0 adds no count.
                if budget is not None:
                    budget.consume_tool_call(
                        0, tool=name, wall_clock_seconds=time.perf_counter() - started
                    )

        toolsets = [_to_pydantic_mcp(s, _mcp_process) for s in (mcp_servers or [])]
        output_type = self._output_type
        if deferred:
            # The leg must be able to end with the collected approval
            # requests; the union keeps the declared output type for
            # the non-paused completion (BL-114).
            from pydantic_ai import DeferredToolRequests

            existing = list(output_type) if isinstance(output_type, list | tuple) else [output_type]
            output_type = [*existing, DeferredToolRequests]
        return Agent(
            self.model,
            output_type=output_type,
            instructions=self._instructions,
            tools=wrapped,
            toolsets=toolsets,
            model_settings=self._model_settings,
        )

    async def run(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
        resume: ResumableState | None = None,
    ) -> Any:
        state = _GuardState(soft_reject_as_error=self._soft_reject_as_error)
        deferred = self._approval_mode == "deferred"
        # Deferred resume inputs are rebuilt once, before the attempt
        # loop: a malformed or undecided state fails loud here, at the
        # call boundary, not mid-retry (BL-114).
        history: Any = None
        results: Any = None
        if deferred and resume is not None:
            history, results = _deferred_resume_inputs(resume)
        policy = self._retry_policy
        tripped = (
            policy is not None
            and policy.circuit_breaker_threshold is not None
            and self._consecutive_failures >= policy.circuit_breaker_threshold
        )
        # One approval-consumption set for the whole run() call, shared
        # across retry attempts: an approval consumed on one attempt must
        # NOT be re-consumable on a retried replay (that would let an
        # approval-gated tool run again under a single human decision).
        # With it shared, a retry that re-hits the gated tool finds no
        # unconsumed approval and re-pauses for a fresh decision.
        used_approvals: set[str] = set()
        # End-to-end wall-clock deadline across attempts + backoff: each
        # attempt is bounded by the *remaining* budget, not a fresh full
        # timeout, so RetryPolicy cannot turn the wall-clock cap into a
        # per-attempt allowance discovered only post-hoc.
        wall_limit = budget.budget.max_wall_clock_seconds if budget is not None else None
        run_started = time.monotonic()
        attempt = 0
        while True:
            if wall_limit is not None:
                remaining = wall_limit - (time.monotonic() - run_started)
                if remaining <= 0:
                    assert budget is not None
                    budget.check_wall_clock()
                    # Boundary fallback (BL-202, BL-189 / BL-167 audit
                    # parity): at the exact instant where the tracker's
                    # strict `>` does not trip but the runtime decides
                    # to terminate, emit the event manually so the
                    # bare raise still pairs with a BudgetExceededEvent
                    # in the audit stream.
                    elapsed = time.monotonic() - run_started
                    budget.emit_wall_clock_exceeded(elapsed)
                    raise BudgetExceeded("wall_clock", wall_limit, elapsed)
            else:
                remaining = None
            agent = self._build_agent(
                tools,
                mcp_servers,
                guard=guard,
                budget=budget,
                resume=resume,
                state=state,
                used_approvals=used_approvals,
                deferred=deferred,
            )

            async def _invoke(agent: Any = agent) -> Any:
                async with agent:
                    if history is not None:
                        # Continuation, not replay (BL-114): the paused
                        # leg's history plus the human decisions; prior
                        # tool calls are already in the history and do
                        # not re-execute.
                        return await agent.run(
                            None,
                            message_history=history,
                            deferred_tool_results=results,
                            deps=deps,
                        )
                    return await agent.run(prompt, deps=deps)

            try:
                result = await self._with_watchdog(_invoke(), budget, timeout_override=remaining)
            except _ApprovalPause:
                return self._resumable(state, prompt)
            except (GovernanceViolation, ApprovalDeniedError):
                raise
            except (asyncio.CancelledError, BudgetExceeded):
                # A wall-clock cancellation or a budget overflow is
                # authoritative and must not be reinterpreted as an
                # approval pause / governance reject just because guard
                # state was also set when the watchdog fired mid-tool.
                # Never retried (a budget is not transient).
                raise
            except BaseException as exc:
                # PydanticAI may have reshaped the in-tool exception.
                if state.pause is not None:
                    return self._resumable(state, prompt)
                if state.governance is not None:
                    raise state.governance from None
                if state.denied is not None:
                    raise state.denied from None
                if (
                    policy is not None
                    and not tripped
                    and policy.retry_on
                    and isinstance(exc, policy.retry_on)
                    and attempt < policy.max_retries
                ):
                    attempt += 1
                    delay = policy.delay_for(attempt)
                    if wall_limit is not None:
                        # Backoff counts against the end-to-end deadline:
                        # never sleep past it (the loop's top check then
                        # raises BudgetExceeded on the next iteration).
                        delay = min(
                            delay,
                            max(0.0, wall_limit - (time.monotonic() - run_started)),
                        )
                    await asyncio.sleep(delay)
                    continue
                if policy is not None and policy.circuit_breaker_threshold is not None:
                    self._consecutive_failures += 1
                raise
            if policy is not None and policy.circuit_breaker_threshold is not None:
                self._consecutive_failures = 0
            break

        if state.pause is not None:
            return self._resumable(state, prompt)

        if budget is not None:
            # In deferred mode this also charges a PAUSED leg's usage:
            # unlike a replay-mode pause (an aborted run with no usage
            # to read), the deferred leg completed and its tokens are
            # real spend; a budget overflow at the pause boundary is
            # authoritative and raises here (BL-114).
            usage = _usage(result)
            tokens = (usage.input_tokens or 0) + (usage.output_tokens or 0)
            if tokens:
                budget.consume_tokens(tokens)
            _surface_cache_tokens(budget, usage)
            budget.consume_step(getattr(usage, "requests", 0) or 0)
            budget.check_wall_clock()
        if deferred:
            from pydantic_ai import DeferredToolRequests

            if isinstance(result.output, DeferredToolRequests):
                if result.output.calls:
                    raise HarnessError(
                        "external tool-execution requests are not supported by the "
                        "harness; only approval requests can defer a run"
                    )
                return self._deferred_resumable(prompt, result.output, result, resume, state)
        return result.output

    async def _with_watchdog(
        self,
        coro: Any,
        budget: BudgetTracker | None,
        *,
        timeout_override: float | None = None,
    ) -> Any:
        """Run ``coro`` under an asyncio.wait_for wall-clock watchdog
        (BL-003).

        wait_for cancels the run task when the deadline passes without
        the adapter needing explicit check_wall_clock calls, so a
        well-behaved agent (which awaits on every model/tool round trip)
        is effectively preempted. The cancellation is still delivered at
        the next await: a fully blocking, CPU-bound or sync-I/O tool
        that never yields control to the event loop is not killed (ADR
        0003's "a pathological tool call that never returns will not be
        killed" still holds for that case; LIMITATIONS L11).

        On timeout, ``budget.check_wall_clock`` is the authoritative
        raise (it emits BudgetExceededEvent with the tracker's own
        elapsed accounting). The fallback only fires in the rare case
        where elapsed rounds exactly to the limit and the strict ``>``
        check does not trip; it reports the real measured elapsed.
        """
        cap = budget.budget.max_wall_clock_seconds if budget is not None else None
        # ``timeout_override`` is the wall-clock budget REMAINING for
        # this attempt (run() bounds each retry by the end-to-end
        # deadline, not a fresh full cap). None preserves the original
        # single-attempt behaviour (the full cap).
        limit = timeout_override if timeout_override is not None else cap
        if limit is None:
            return await coro
        limit = max(0.0, limit)
        start = time.monotonic()
        try:
            return await asyncio.wait_for(coro, timeout=limit)
        except TimeoutError:
            assert budget is not None
            elapsed = time.monotonic() - start
            budget.check_wall_clock()
            # Boundary fallback (BL-202): the wait_for timer can fire
            # when the tracker's datetime accounting has not yet
            # ticked past the cap. Emit so the bare raise still pairs
            # with the audit stream (BL-189 / BL-167 class).
            budget.emit_wall_clock_exceeded(elapsed)
            raise BudgetExceeded("wall_clock", cap if cap is not None else limit, elapsed) from None

    def _resumable(self, state: _GuardState, prompt: str) -> ResumableState:
        assert state.pause is not None
        return ResumableState(
            contract_name=self.name,
            contract_version="",
            workload=self.name,
            input_payload={"prompt": prompt},
            pending_approvals=[state.pause],
            trace_id=uuid.uuid4().hex,
        )

    def _deferred_resumable(
        self,
        prompt: str,
        requests: Any,
        result: Any,
        resume: ResumableState | None,
        state: _GuardState,
    ) -> ResumableState:
        """Build the pause state for a deferred leg (BL-114).

        Interruption ids are the run's own tool_call_ids (stable
        handles minted with the message history, unlike the per-check
        guard ids of replay mode), so the caller's decisions map
        directly onto DeferredToolResults; the (tool, arguments)
        binding is still verified at execution time (BL-193). The
        original prompt and trace_id carry forward across re-pauses so
        a multi-pause run stays one correlated conversation. Each
        interruption also carries the tier / rollback plan the gate
        recorded for that tool_call_id (BL-251), matching the replay
        path's per-interruption approval context.
        """
        from pydantic_ai.messages import ModelMessagesTypeAdapter

        now = datetime.now(UTC)
        pending = []
        for tc in requests.approvals:
            tier, rollback_plan = state.approval_context.get(tc.tool_call_id, (None, None))
            pending.append(
                ApprovalInterruption(
                    id=tc.tool_call_id,
                    created_at=now,
                    tool=tc.tool_name,
                    arguments=tc.args_as_dict(),
                    tier=tier,
                    rollback_plan=rollback_plan,
                )
            )
        payload = dict(resume.input_payload) if resume is not None else {"prompt": prompt}
        return ResumableState(
            contract_name=self.name,
            contract_version="",
            workload=self.name,
            input_payload=payload,
            pending_approvals=pending,
            trace_id=resume.trace_id if resume is not None else uuid.uuid4().hex,
            runtime_state={
                "mode": "deferred",
                "messages": ModelMessagesTypeAdapter.dump_python(
                    result.all_messages(), mode="json"
                ),
            },
        )

    async def stream(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
        resume: ResumableState | None = None,
    ) -> AsyncIterator[Any]:
        state = _GuardState(soft_reject_as_error=self._soft_reject_as_error)
        agent = self._build_agent(
            tools,
            mcp_servers,
            guard=guard,
            budget=budget,
            resume=resume,
            state=state,
            used_approvals=set(),
        )
        consumed = 0

        def _reconcile(stream: Any) -> None:
            """Consume any newly-reported tokens; raises on cap crossing."""
            nonlocal consumed
            if budget is None:
                return
            usage = _usage(stream)
            total = (usage.input_tokens or 0) + (usage.output_tokens or 0)
            delta = total - consumed
            if delta > 0:
                consumed = total
                budget.consume_tokens(delta)

        try:
            async with agent, agent.run_stream(prompt, deps=deps) as stream:
                async for chunk in stream.stream_text(delta=True):
                    # Budget parity with run(): reactive wall-clock at
                    # each chunk boundary plus incremental token usage;
                    # either raises BudgetExceeded the moment a cap is
                    # crossed (a generator cannot be wrapped in the
                    # preemptive wait_for watchdog, so enforcement is at
                    # the chunk checkpoint, per ADR 0003's reactive rule).
                    if budget is not None:
                        budget.check_wall_clock()
                    _reconcile(stream)
                    yield chunk
                # Final reconciliation: some models (and TestModel) only
                # finalize usage once the stream is fully consumed.
                _reconcile(stream)
                if budget is not None:
                    usage = _usage(stream)
                    # Cache counts are surfaced once at the final
                    # reconciliation, like step charging: providers
                    # finalize cache accounting with the run-level
                    # usage, not per chunk (BL-132).
                    _surface_cache_tokens(budget, usage)
                    budget.consume_step(getattr(usage, "requests", 0) or 0)
        except _ApprovalPause as exc:
            # Streaming has no resumable handoff (the generator cannot
            # surface a ResumableState and resume cleanly). Translate
            # the private sentinel into a clear public contract error;
            # approval-gated tools must use run().
            raise HarnessError(
                f"tool {exc.interruption.tool!r} requires approval; "
                "approval-gated tools are not supported in streaming "
                "mode -- use run()"
            ) from None
        if state.governance is not None:
            raise state.governance
        if state.denied is not None:
            raise state.denied
