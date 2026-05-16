"""The enforcement loop.

run_under_contract is the single entry point. It validates preconditions
and invariants, constructs a BudgetTracker and HarnessToolGuard when
applicable, calls the runtime with those plus MCP server specs, parses
the output, validates postconditions, emits structured events throughout.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from harness.budgets import ActionBudget, BudgetTracker
from harness.contract import Contract, Severity
from harness.errors import (
    InvariantViolation,
    PostconditionViolation,
    PreconditionViolation,
)
from harness.events import (
    ContractCompleted,
    ContractStarted,
    InvariantViolated,
    PostconditionViolated,
    PreconditionViolated,
)
from harness.guard import HarnessToolGuard, ToolGuard
from harness.interruption import ResumableState
from harness.mcp import MCPServerSpec
from harness.runtime import Runtime
from harness.sinks import EventSink, NullSink

__all__ = ["run_under_contract"]


async def run_under_contract[InputT: BaseModel, OutputT: BaseModel](
    runtime: Runtime,
    contract: Contract[InputT, OutputT],
    input: InputT,
    output_model: type[OutputT],
    *,
    deps: Any | None = None,
    sink: EventSink | None = None,
    resume: ResumableState | None = None,
    invariant_state: Any | None = None,
    budget: ActionBudget | None = None,
    mcp_servers: list[MCPServerSpec] | None = None,
    guard: ToolGuard | None = None,
) -> OutputT | ResumableState:
    """Execute a workload under contract.

    Args:
        runtime: The Runtime adapter that invokes the agent framework.
        contract: The behavioral contract to enforce.
        input: Pydantic model carrying the workload input.
        output_model: Pydantic model class to parse the runtime result into.
        deps: Dependencies passed to the runtime.
        sink: Where to emit structured events. Defaults to NullSink.
        resume: If provided, a previous ResumableState being continued
            after human approval. Pending approvals must be resolved
            (not pending) before this is accepted.
        invariant_state: Optional observable state for invariants. If
            None, invariants are checked against the input.
        budget: ActionBudget spec. If provided, a BudgetTracker is
            constructed and passed to the runtime. The runtime adapter
            is responsible for calling consume_* methods.
        mcp_servers: MCP server specs to pass to the runtime. The
            adapter handles lifecycle.
        guard: Tool guard. If None and the contract has governance
            predicates or approval_required entries, a HarnessToolGuard
            is constructed from the contract.

    Returns:
        OutputT on successful completion.
        ResumableState if execution is interrupted by an approval request.

    Raises:
        PreconditionViolation: A hard precondition failed.
        PostconditionViolation: A hard postcondition failed.
        InvariantViolation: A hard invariant failed.
        BudgetExceeded: An action budget was exceeded.
        GovernanceViolation: A hard governance predicate failed.
        ValueError: A resume state has unresolved pending approvals.
    """
    active_sink: EventSink = sink if sink is not None else NullSink()
    trace_id = resume.trace_id if resume is not None else uuid.uuid4().hex
    span_id = uuid.uuid4().hex
    started_at = datetime.now(UTC)

    base = {
        "workload": contract.name,
        "contract": contract.name,
        "contract_version": contract.version,
        "trace_id": trace_id,
        "span_id": span_id,
    }

    active_sink.emit(ContractStarted(timestamp=started_at, **base))

    if resume is not None:
        unresolved = [ai for ai in resume.pending_approvals if ai.decision == "pending"]
        if unresolved:
            raise ValueError(f"Cannot resume: {len(unresolved)} approvals still pending")

    # 1. Preconditions
    for pred_pre in contract.preconditions:
        if not pred_pre(input):
            active_sink.emit(
                PreconditionViolated(
                    timestamp=datetime.now(UTC),
                    predicate=pred_pre.name,
                    severity=pred_pre.severity,
                    state_snapshot=input.model_dump(mode="json"),
                    **base,
                )
            )
            if pred_pre.severity == Severity.HARD:
                raise PreconditionViolation(pred_pre.name)

    # 2. Invariants (pre-run check; in-loop checks delegated to runtime)
    inv_state: Any = invariant_state if invariant_state is not None else input
    for pred_inv in contract.invariants:
        if not pred_inv(inv_state):
            active_sink.emit(
                InvariantViolated(
                    timestamp=datetime.now(UTC),
                    predicate=pred_inv.name,
                    severity=pred_inv.severity,
                    state_snapshot={},
                    **base,
                )
            )
            if pred_inv.severity == Severity.HARD:
                raise InvariantViolation(pred_inv.name)

    # 3. Construct per-run mutable objects
    tracker: BudgetTracker | None = None
    if budget is not None:
        tracker = BudgetTracker(budget, sink=active_sink, base_event_fields=base)

    active_guard: ToolGuard | None = guard
    if active_guard is None and (contract.governance or contract.approval_required):
        active_guard = HarnessToolGuard(contract, sink=active_sink, base_event_fields=base)

    # 4. Runtime invocation
    result = await runtime.run(
        prompt=input.model_dump_json(),
        deps=deps,
        budget=tracker,
        mcp_servers=mcp_servers,
        guard=active_guard,
    )

    # 5. Parse output
    if isinstance(result, output_model):
        output: OutputT = result
    else:
        output = output_model.model_validate(result)

    # 6. Postconditions
    for pred_post in contract.postconditions:
        if not pred_post(output):
            active_sink.emit(
                PostconditionViolated(
                    timestamp=datetime.now(UTC),
                    predicate=pred_post.name,
                    severity=pred_post.severity,
                    state_snapshot=output.model_dump(mode="json"),
                    **base,
                )
            )
            if pred_post.severity == Severity.HARD:
                raise PostconditionViolation(pred_post.name)

    completed_at = datetime.now(UTC)
    duration_ms = (completed_at - started_at).total_seconds() * 1000.0
    active_sink.emit(
        ContractCompleted(
            timestamp=completed_at,
            duration_ms=duration_ms,
            **base,
        )
    )

    return output
