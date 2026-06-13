"""The enforcement loop.

run_under_contract is the single entry point. It validates preconditions
and invariants, constructs a BudgetTracker and HarnessToolGuard when
applicable, calls the runtime with those plus MCP server specs, parses
the output, validates postconditions, emits structured events throughout.

L3 default-path wiring (ADR 0010), all opt-in and additive (omitting
every new keyword reproduces the exact L1/L2 behaviour):

- BL-100: ``skill_contracts`` composes a workload's loaded skill
  contracts with its workload contract before enforcement.
- BL-101: ``drift_monitor`` records each predicate pass/fail and, with
  ``drift_threshold``, emits DriftThresholdCrossed when a snapshotted
  reference diverges past the threshold.
- BL-102: a soft postcondition's RecoveryHandler can drive control flow
  (retry / substitute / escalate) via RecoveryOutcome.directive, not
  only emit-and-continue.
- BL-104: ``lifecycles`` are async context managers (e.g. a
  memory.TTLSweeper) entered for the duration of the run.
- BL-154: on a resume, the BudgetTracker is seeded from the consumed
  totals carried on the ResumableState so budgets accumulate across an
  approval pause instead of restarting at zero.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, suppress
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from harness.authority import AuthorityTier, TierClassifier
from harness.budgets import ActionBudget, BudgetTracker
from harness.composition import compose_contracts
from harness.contract import Contract, Severity
from harness.drift import DriftMonitor
from harness.errors import (
    ApprovalDenied,
    BudgetExceeded,
    GovernanceViolation,
    InvariantViolation,
    PostconditionViolation,
    PreconditionViolation,
)
from harness.events import (
    ContractCompleted,
    ContractStarted,
    DriftThresholdCrossed,
    InvariantViolated,
    PostconditionViolated,
    PreconditionViolated,
    RecoveryApplied,
)
from harness.guard import HarnessToolGuard, ToolGuard
from harness.interruption import ResumableState
from harness.mcp import MCPServerSpec
from harness.provenance import RunOutcome, RunRecord, contract_digest
from harness.recovery import RecoveryHandler, RecoveryOutcome
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
    recovery: Mapping[str, RecoveryHandler] | None = None,
    skill_contracts: Sequence[Contract[Any, Any]] | None = None,
    drift_monitor: DriftMonitor | None = None,
    drift_threshold: float | None = None,
    lifecycles: Sequence[AbstractAsyncContextManager[Any]] | None = None,
    parent_span_id: str | None = None,
    record_sink: Callable[[RunRecord], None] | None = None,
    tier_classifier: TierClassifier | None = None,
    approval_tier: AuthorityTier = AuthorityTier.STATEFUL,
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
            (not pending) before this is accepted. Its consumed budget
            totals seed the resumed run's tracker (BL-154).
        invariant_state: Optional observable state for invariants. If
            None, invariants are checked against the input.
        budget: ActionBudget spec. If provided, a BudgetTracker is
            constructed and passed to the runtime. The runtime adapter
            is responsible for calling consume_* methods.
        mcp_servers: MCP server specs to pass to the runtime. The
            adapter handles lifecycle.
        guard: Tool guard. If None and the contract has governance
            predicates or approval_required entries, or a tier_classifier
            is supplied, a HarnessToolGuard is constructed from the
            (composed) contract.
        tier_classifier: Optional blast-radius TierClassifier (BL-242).
            When supplied (and no explicit ``guard`` is given), the
            default HarnessToolGuard escalates an action classified at
            ``approval_tier`` or above to REQUIRE_APPROVAL, beyond the
            static approval_required list. None preserves L1.
        approval_tier: The AuthorityTier at or above which an action
            requires approval when a ``tier_classifier`` is active
            (default STATEFUL). Ignored without a classifier.
        recovery: Optional map of predicate name -> RecoveryHandler
            (BL-061). On a SOFT pre/invariant/post violation whose
            predicate name is in the map, the handler runs and a
            RecoveryApplied event is emitted. On the postcondition stage
            the handler's RecoveryOutcome.directive can drive control
            flow (BL-102): retry the runtime once, substitute the
            output, or escalate to a hard violation. Elsewhere the run
            still soft-continues. A handler that raises is contained: a
            RecoveryApplied(recovered=False) is emitted and the soft
            path continues (a soft violation never halts). None
            preserves L1 behaviour.
        skill_contracts: Skill-shipped contracts to compose with
            ``contract`` before enforcement (BL-100). Composition is the
            ADR 0002 rule (capability obligations intersected, safety
            obligations unioned). None preserves L1 (no composition).
        drift_monitor: If provided, each predicate evaluation is
            recorded as pass/fail per predicate name (BL-101). The
            caller owns snapshot_reference() / baseline policy.
        drift_threshold: With ``drift_monitor``, emit
            DriftThresholdCrossed when a predicate's live JSD exceeds
            this value. None records distributions without alerting.
        lifecycles: Async context managers entered around the run and
            exited after it (BL-104), e.g. a memory.TTLSweeper bound to
            the run's lifetime. Entered in order, exited in reverse.
        parent_span_id: Optional OTel parent span id stamped onto every
            emitted event so a workload that runs ``run_under_contract``
            from inside another contract produces a correlated span
            tree instead of flat siblings. None (the default) preserves
            the prior behaviour (events carry ``parent_span_id=None``).
        record_sink: If provided, a callable invoked exactly once at the
            run's terminal point with a RunRecord stamping the digest of
            the (composed) contract that enforced the run, its outcome,
            and timing (BL-185, ADR 0012). The digest is taken from the
            live contract object here, so the attestation is bound to
            what actually ran. None (the default) preserves prior
            behaviour (no record is produced).

    Returns:
        OutputT on successful completion.
        ResumableState if execution is interrupted by an approval
        request; it carries the consumed budget totals (BL-154).

    Raises:
        PreconditionViolation: A hard precondition failed.
        PostconditionViolation: A hard postcondition failed (or a soft
            one a recovery handler escalated).
        InvariantViolation: A hard invariant failed.
        BudgetExceeded: An action budget was exceeded.
        GovernanceViolation: A hard governance predicate failed.
        ValueError: A resume state has unresolved pending approvals.
    """
    # Resume validation runs FIRST (BL-203, BL-167 class extension):
    # an unresolved approval is a caller-shape error that does not
    # depend on the contract / sink / trace_id, so raising it AFTER
    # emitting ``ContractStarted`` produced an orphan event in every
    # downstream sink with no matching terminal event and no
    # RunRecord (the run-provenance gate). Moving the check above
    # the first emit guarantees that any run that emits
    # ``ContractStarted`` also emits a terminal event.
    if resume is not None:
        _unresolved = [ai for ai in resume.pending_approvals if ai.decision == "pending"]
        if _unresolved:
            raise ValueError(f"Cannot resume: {len(_unresolved)} approvals still pending")

    active_sink: EventSink = sink if sink is not None else NullSink()

    if skill_contracts:
        contract = compose_contracts(contract.name, contract.version, contract, *skill_contracts)

    trace_id = resume.trace_id if resume is not None else uuid.uuid4().hex
    span_id = uuid.uuid4().hex
    started_at = datetime.now(UTC)

    base = {
        "workload": contract.name,
        "contract": contract.name,
        "contract_version": contract.version,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
    }

    active_sink.emit(ContractStarted(timestamp=started_at, **base))

    digest = contract_digest(contract)

    def _emit_record(outcome: RunOutcome) -> None:
        """Emit the run-provenance record at a terminal point (BL-185).

        No-op unless the caller opted in via ``record_sink``. The digest
        is bound to the contract object that actually enforced this run
        (post-composition), not reconstructed afterwards.
        """
        if record_sink is None:
            return
        now = datetime.now(UTC)
        record_sink(
            RunRecord(
                run_id=trace_id,
                workload=contract.name,
                contract_name=contract.name,
                contract_version=contract.version,
                contract_digest=digest,
                outcome=outcome,
                started_at=started_at.isoformat(),
                completed_at=now.isoformat(),
                duration_ms=(now - started_at).total_seconds() * 1000.0,
            )
        )

    def _record(predicate: str, ok: bool, stage: str) -> None:
        """Feed the drift monitor and alert on a threshold crossing."""
        if drift_monitor is None:
            return
        drift_monitor.record(predicate, "pass" if ok else "fail")
        if drift_threshold is None:
            return
        divergence = drift_monitor.drift(predicate)
        if divergence > drift_threshold:
            active_sink.emit(
                DriftThresholdCrossed(
                    timestamp=datetime.now(UTC),
                    predicate=predicate,
                    stage=stage,
                    divergence=divergence,
                    threshold=drift_threshold,
                    **base,
                )
            )

    async def _recover(predicate: str, stage: str, state: Any) -> RecoveryOutcome | None:
        """Run the registered handler for a soft violation, if any.

        A handler that raises is contained (a soft violation must never
        halt the run, with or without a handler): a
        RecoveryApplied(recovered=False) is emitted and None is
        returned, so the soft path continues unchanged.
        """
        if recovery is None:
            return None
        handler = recovery.get(predicate)
        if handler is None:
            return None
        try:
            outcome = await handler.recover(predicate=predicate, stage=stage, state=state)
        except Exception as exc:
            active_sink.emit(
                RecoveryApplied(
                    timestamp=datetime.now(UTC),
                    predicate=predicate,
                    stage=stage,
                    action=f"recovery handler raised: {exc!r}",
                    recovered=False,
                    **base,
                )
            )
            return None
        active_sink.emit(
            RecoveryApplied(
                timestamp=datetime.now(UTC),
                predicate=predicate,
                stage=stage,
                action=outcome.action,
                recovered=outcome.recovered,
                **base,
            )
        )
        return outcome

    # 1. Preconditions
    for pred_pre in contract.preconditions:
        ok = pred_pre(input)
        _record(pred_pre.name, ok, "precondition")
        if not ok:
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
                _emit_record("precondition")
                raise PreconditionViolation(pred_pre.name)
            await _recover(pred_pre.name, "precondition", input)

    # 2. Invariants (pre-run check; in-loop checks delegated to runtime)
    inv_state: Any = invariant_state if invariant_state is not None else input
    for pred_inv in contract.invariants:
        ok = pred_inv(inv_state)
        _record(pred_inv.name, ok, "invariant")
        if not ok:
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
                _emit_record("invariant")
                raise InvariantViolation(pred_inv.name)
            await _recover(pred_inv.name, "invariant", inv_state)

    # 3. Construct per-run mutable objects. On a resume the tracker is
    # seeded from the consumed totals carried on the ResumableState so
    # budgets accumulate across the approval pause (BL-154).
    tracker: BudgetTracker | None = None
    if budget is not None:
        seed: dict[str, Any] = {}
        if resume is not None:
            seed = {
                "initial_steps": resume.consumed_steps,
                "initial_tokens": resume.consumed_tokens,
                "initial_tool_calls": resume.consumed_tool_calls,
                "initial_per_tool": dict(resume.consumed_per_tool),
                "initial_per_tool_tokens": dict(resume.consumed_per_tool_tokens),
                "initial_per_tool_seconds": dict(resume.consumed_per_tool_seconds),
                "initial_cost_usd": resume.consumed_cost_usd,
            }
        tracker = BudgetTracker(budget, sink=active_sink, base_event_fields=base, **seed)

    active_guard: ToolGuard | None = guard
    if active_guard is None and (
        contract.governance or contract.approval_required or tier_classifier is not None
    ):
        active_guard = HarnessToolGuard(
            contract,
            sink=active_sink,
            base_event_fields=base,
            tier_classifier=tier_classifier,
            approval_tier=approval_tier,
        )

    def _finalize_resumable(state: ResumableState) -> ResumableState:
        # The harness owns the contract boundary, so it (not the runtime
        # adapter) is the source of truth for identity and trace_id.
        # Reusing this run's trace_id keeps the audit trail and resume on
        # one trace; the budget snapshot makes the resumed run cumulative
        # (BL-154). The guard already emitted ApprovalRequested.
        update: dict[str, Any] = {
            "contract_name": contract.name,
            "contract_version": contract.version,
            "workload": contract.name,
            "input_payload": input.model_dump(mode="json"),
            "trace_id": trace_id,
        }
        if tracker is not None:
            update.update(tracker.snapshot())
        _emit_record("paused")
        return state.model_copy(update=update)

    async def _invoke(*, resume_state: ResumableState | None) -> Any:
        try:
            return await runtime.run(
                prompt=input.model_dump_json(),
                deps=deps,
                budget=tracker,
                mcp_servers=mcp_servers,
                guard=active_guard,
                resume=resume_state,
            )
        except GovernanceViolation:
            _emit_record("governance")
            raise
        except BudgetExceeded:
            _emit_record("budget")
            raise
        except ApprovalDenied:
            _emit_record("approval_denied")
            raise

    async with AsyncExitStack() as stack:
        for lifecycle in lifecycles or ():
            await stack.enter_async_context(lifecycle)

        # 4. Runtime invocation (the first leg continues any approval
        # pause via `resume`).
        result = await _invoke(resume_state=resume)

        # 4a. An approval pause short-circuits (BL-002), carrying the
        # budget snapshot for a cumulative resume (BL-154).
        if isinstance(result, ResumableState):
            return _finalize_resumable(result)

        # 5/6. Parse output, validate postconditions, honour any
        # postcondition recovery directive (BL-102). A "retry" directive
        # re-invokes the runtime at most once. The retry does NOT carry
        # `resume`: a retry is a fresh attempt to satisfy a postcondition,
        # not a continuation of the approved pause, so re-passing `resume`
        # would let the retried run re-consume a prior approval and run
        # an approval-gated tool again without fresh human approval. With
        # resume=None the retry re-pauses (returns a ResumableState) if it
        # hits an approval-gated tool again, requiring a new decision.
        retried = False
        post_records: list[tuple[str, bool]] = []

        def _flush_post() -> None:
            """Record this leg's postcondition outcomes into the drift
            monitor exactly once (BL-101).

            A leg abandoned for a retry (BL-102) must not contribute:
            re-running every postcondition on the retried leg would
            otherwise double-count each predicate and skew the JSD
            signal. Flushed at each terminal point of a leg (a
            hard/escalate raise, or a final no-retry completion) and
            discarded when the leg is retried.
            """
            for _name, _ok in post_records:
                _record(_name, _ok, "postcondition")

        while True:
            if isinstance(result, output_model):
                output: OutputT = result
            else:
                try:
                    output = output_model.model_validate(result)
                except ValidationError:
                    _emit_record("output_invalid")
                    raise

            retry_requested = False
            post_records.clear()
            for pred_post in contract.postconditions:
                ok = pred_post(output)
                post_records.append((pred_post.name, ok))
                if ok:
                    continue
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
                    _flush_post()
                    _emit_record("postcondition")
                    raise PostconditionViolation(pred_post.name)
                outcome = await _recover(pred_post.name, "postcondition", output)
                if outcome is None or outcome.directive == "continue":
                    continue
                if outcome.directive == "escalate":
                    _flush_post()
                    _emit_record("postcondition")
                    raise PostconditionViolation(pred_post.name, "escalated by recovery handler")
                if outcome.directive == "substitute":
                    # An invalid substitution is ignored; the soft
                    # violation falls back to emit-and-continue.
                    with suppress(ValidationError, ValueError):
                        output = (
                            outcome.replacement
                            if isinstance(outcome.replacement, output_model)
                            else output_model.model_validate(outcome.replacement)
                        )
                    continue
                if outcome.directive == "retry" and not retried:
                    retry_requested = True
                    break
                # retry exhausted: soft-continue, as before.

            if not retry_requested:
                _flush_post()
                break
            retried = True
            result = await _invoke(resume_state=None)
            if isinstance(result, ResumableState):
                return _finalize_resumable(result)

    completed_at = datetime.now(UTC)
    duration_ms = (completed_at - started_at).total_seconds() * 1000.0
    active_sink.emit(
        ContractCompleted(
            timestamp=completed_at,
            duration_ms=duration_ms,
            **base,
        )
    )

    _emit_record("completed")
    return output
