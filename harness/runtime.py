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
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import time
import uuid
import warnings
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from harness.budgets import BudgetTracker
from harness.contract import Severity
from harness.errors import ApprovalDenied as ApprovalDeniedError
from harness.errors import BudgetExceeded, GovernanceViolation, HarnessError
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
    occurrence regardless of ``retry_on``. Budget counters are shared
    across attempts (the tracker is constructed once by the harness), so
    retries cannot be used to evade a budget.

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
        # BL-137: when set, a soft governance reject is raised as a
        # framework tool-retry error (a typed rejection the model sees
        # as an error) instead of returned as the tool's string value.
        self.soft_reject_as_error = soft_reject_as_error


def _usage(result: Any) -> Any:
    """Read PydanticAI run usage.

    1.97 exposes ``usage`` as a property (the legacy ``usage()`` method
    is deprecated); the locked version is 1.97.0, so access the property
    directly and avoid the DeprecationWarning the call form emits.
    """
    return result.usage


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
    used: set[str],
) -> ApprovalInterruption | None:
    """First not-yet-consumed resolved approval for ``tool`` in ``resume``."""
    if resume is None:
        return None
    for ai in resume.pending_approvals:
        if ai.tool == tool and ai.id not in used and ai.decision != "pending":
            used.add(ai.id)
            return ai
    return None


async def _gate(
    name: str,
    arguments: dict[str, Any],
    *,
    guard: ToolGuard | None,
    budget: BudgetTracker | None,
    resume: ResumableState | None,
    state: _GuardState,
    used_approvals: set[str],
) -> str | None:
    """Guard + budget gate for one proposed tool call.

    Returns a soft-reject message to surface to the model, or None to
    proceed. Raises GovernanceViolation (hard reject), _ApprovalPause
    (approval needed, no decision yet), or ApprovalDenied. Used for both
    locally-defined tools and MCP-exposed tools so neither bypasses
    governance / budget (BL-001/073).
    """
    if guard is not None:
        response = await guard.check(name, arguments)
        if response.decision == GuardDecision.REJECT:
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
        if response.decision == GuardDecision.REQUIRE_APPROVAL:
            decided = _resolved_decision(resume, name, used_approvals)
            if decided is None:
                interruption = ApprovalInterruption(
                    id=response.interruption_id or uuid.uuid4().hex,
                    created_at=datetime.now(UTC),
                    tool=name,
                    arguments=arguments,
                )
                state.pause = interruption
                raise _ApprovalPause(interruption)
            if decided.decision == "denied":
                state.denied = ApprovalDeniedError(name, decided.decision_reason)
                raise state.denied
    if budget is not None:
        budget.consume_tool_call(tool=name)
    return None


def _wrap_tool(
    tool: Any,
    *,
    guard: ToolGuard | None,
    budget: BudgetTracker | None,
    resume: ResumableState | None,
    state: _GuardState,
    used_approvals: set[str],
) -> Any:
    """Wrap a tool so the guard and budget run before its body.

    The wrapper preserves the original signature and annotations so
    PydanticAI still infers the correct tool JSON schema.
    """
    func = getattr(tool, "function", tool)
    name = _tool_name(tool)
    is_async = inspect.iscoroutinefunction(func)

    async def _local_gate(arguments: dict[str, Any]) -> str | None:
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
        soft = await _local_gate(kwargs)
        if soft is not None:
            return soft
        started = time.perf_counter()
        try:
            result = func(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result
        finally:
            _charge_wall_clock(started)

    @functools.wraps(func)
    async def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        soft = await _local_gate(kwargs)
        if soft is not None:
            return soft
        started = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            _charge_wall_clock(started)

    wrapper = _async_wrapper if is_async else _sync_wrapper
    with contextlib.suppress(ValueError, TypeError):
        wrapper.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
    wrapper.__annotations__ = dict(getattr(func, "__annotations__", {}))
    return wrapper


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
    ) -> None:
        self.model = model
        self._output_type = output_type
        self._instructions = instructions
        self._retry_policy = retry_policy
        self._soft_reject_as_error = soft_reject_as_error
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
    ) -> Any:
        from pydantic_ai import Agent

        wrapped = [
            _wrap_tool(
                t,
                guard=guard,
                budget=budget,
                resume=resume,
                state=state,
                used_approvals=used_approvals,
            )
            for t in (tools or [])
        ]

        async def _mcp_process(
            ctx: Any, call_tool: Any, name: str, tool_args: dict[str, Any]
        ) -> Any:
            # Same guard + budget gate as local tools, so MCP tool calls
            # cannot bypass governance/approval/budget (BL-001/073).
            soft = await _gate(
                name,
                tool_args,
                guard=guard,
                budget=budget,
                resume=resume,
                state=state,
                used_approvals=used_approvals,
            )
            if soft is not None:
                return soft
            started = time.perf_counter()
            try:
                return await call_tool(name, tool_args)
            finally:
                # Per-tool wall-clock parity with local tools (BL-123);
                # the gate already counted the call. n=0 adds no count.
                if budget is not None:
                    budget.consume_tool_call(
                        0, tool=name, wall_clock_seconds=time.perf_counter() - started
                    )

        toolsets = [_to_pydantic_mcp(s, _mcp_process) for s in (mcp_servers or [])]
        return Agent(
            self.model,
            output_type=self._output_type,
            instructions=self._instructions,
            tools=wrapped,
            toolsets=toolsets,
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
        policy = self._retry_policy
        tripped = (
            policy is not None
            and policy.circuit_breaker_threshold is not None
            and self._consecutive_failures >= policy.circuit_breaker_threshold
        )
        attempt = 0
        while True:
            used_approvals: set[str] = set()
            agent = self._build_agent(
                tools,
                mcp_servers,
                guard=guard,
                budget=budget,
                resume=resume,
                state=state,
                used_approvals=used_approvals,
            )

            async def _invoke(agent: Any = agent) -> Any:
                async with agent:
                    return await agent.run(prompt, deps=deps)

            try:
                result = await self._with_watchdog(_invoke(), budget)
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
                    await asyncio.sleep(policy.delay_for(attempt))
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
            usage = _usage(result)
            tokens = (usage.input_tokens or 0) + (usage.output_tokens or 0)
            if tokens:
                budget.consume_tokens(tokens)
            budget.consume_step(getattr(usage, "requests", 0) or 0)
            budget.check_wall_clock()
        return result.output

    async def _with_watchdog(self, coro: Any, budget: BudgetTracker | None) -> Any:
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
        limit = budget.budget.max_wall_clock_seconds if budget is not None else None
        if limit is None:
            return await coro
        start = time.monotonic()
        try:
            return await asyncio.wait_for(coro, timeout=limit)
        except TimeoutError:
            assert budget is not None
            elapsed = time.monotonic() - start
            budget.check_wall_clock()
            raise BudgetExceeded("wall_clock", limit, elapsed) from None

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
