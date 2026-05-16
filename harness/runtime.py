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
- BL-003: a background watchdog enforces wall-clock preemptively.
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
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from harness.budgets import BudgetTracker
from harness.contract import Severity
from harness.errors import ApprovalDenied as ApprovalDeniedError
from harness.errors import BudgetExceeded, GovernanceViolation, HarnessError
from harness.guard import GuardDecision, ToolGuard
from harness.interruption import ApprovalInterruption, ResumableState
from harness.mcp import MCPServerSpec, MCPTransport

__all__ = ["PydanticAIRuntime", "Runtime"]


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

    def __init__(self) -> None:
        self.pause: ApprovalInterruption | None = None
        self.governance: GovernanceViolation | None = None
        self.denied: ApprovalDeniedError | None = None


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

    async def _gate(arguments: dict[str, Any]) -> str | None:
        """Return a soft-reject message to surface, or None to proceed.

        Raises on hard reject / approval pause / denial.
        """
        if guard is not None:
            response = await guard.check(name, arguments)
            if response.decision == GuardDecision.REJECT:
                if response.severity == Severity.HARD:
                    state.governance = GovernanceViolation(response.reason or "governance", name)
                    raise state.governance
                return f"[blocked: {response.reason or 'governance predicate failed'}]"
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

    @functools.wraps(func)
    async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
        soft = await _gate(kwargs)
        if soft is not None:
            return soft
        result = func(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    @functools.wraps(func)
    async def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        soft = await _gate(kwargs)
        if soft is not None:
            return soft
        return func(*args, **kwargs)

    wrapper = _async_wrapper if is_async else _sync_wrapper
    with contextlib.suppress(ValueError, TypeError):
        wrapper.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
    wrapper.__annotations__ = dict(getattr(func, "__annotations__", {}))
    return wrapper


def _to_pydantic_mcp(spec: MCPServerSpec) -> Any:
    """Translate an MCPServerSpec into a PydanticAI MCP toolset.

    Honours the spec's ``headers`` (HTTP/SSE auth) and ``allowed_tools``
    allowlist -- both are validated manifest surface; dropping them
    would silently bypass workload-declared MCP restrictions.
    """
    from pydantic_ai.mcp import MCPServerSSE, MCPServerStdio, MCPServerStreamableHTTP

    server: Any
    if spec.transport == MCPTransport.STDIO:
        server = MCPServerStdio(
            command=spec.command or "",
            args=list(spec.args),
            timeout=spec.timeout_seconds,
        )
    elif spec.transport == MCPTransport.SSE:
        server = MCPServerSSE(
            url=spec.url or "",
            headers=dict(spec.headers),
            timeout=spec.timeout_seconds,
        )
    else:
        # 1.97 deprecates the explicit class in favour of MCPToolset(url)
        # but keeps it functional through the pinned range; the Protocol
        # boundary (ADR 0007) absorbs this churn. Silence the local
        # DeprecationWarning rather than chase a pre-v2 API rename.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            server = MCPServerStreamableHTTP(
                url=spec.url or "",
                headers=dict(spec.headers),
                timeout=spec.timeout_seconds,
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
    ) -> None:
        self.model = model
        self._output_type = output_type
        self._instructions = instructions

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
        toolsets = [_to_pydantic_mcp(s) for s in (mcp_servers or [])]
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
        state = _GuardState()
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

        async def _invoke() -> Any:
            async with agent:
                return await agent.run(prompt, deps=deps)

        try:
            result = await self._with_watchdog(_invoke(), budget)
        except _ApprovalPause:
            return self._resumable(state, prompt)
        except (GovernanceViolation, ApprovalDeniedError):
            raise
        except BaseException:
            # PydanticAI may have reshaped the in-tool exception.
            if state.pause is not None:
                return self._resumable(state, prompt)
            if state.governance is not None:
                raise state.governance from None
            if state.denied is not None:
                raise state.denied from None
            raise

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
        """Run ``coro`` under a preemptive wall-clock watchdog (BL-003).

        asyncio.wait_for cancels the run task the moment the deadline
        passes, instead of waiting for the next cooperative checkpoint.
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
        state = _GuardState()
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
