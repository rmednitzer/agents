# ADR 0033: Two-step parameter restatement for irreversible actions (BL-252)

- Status: Accepted
- Date: 2026-06-13
- Authors: rmednitzer
- Builds on: ADR 0027 (BL-193 argument binding, deferred resume),
  ADR 0029 (authority tiers, BL-242), ADR 0031 (approval-context
  payload, BL-251)

## Context

ADR 0031 (BL-251) delivered the data-carrying half of the
graduated-authority approval context: the `tier` and a `rollback_plan`
now travel onto the human-facing `ApprovalInterruption`. It split the
*behavioural* half forward to BL-252, which named two refinements: a
two-step "restate the parameters" confirmation for an irreversible
(Tier 3) action, and an evidence-capture hook around its execution.

This ADR delivers the **two-step restatement**. The operator gateway
confirms an irreversible action with the parameters restated, so a human
cannot rubber-stamp a Tier 3 approval without re-reading what they are
authorising. BL-193 (ADR 0027) already binds an approval to the
`(tool, arguments)` tuple at execution; restatement adds a second,
*independent* human-supplied copy of the arguments that must match.

The evidence-capture hook is split forward to **BL-253** (below): it
wraps the *execution* of an approved Tier 3 tool, which touches the
tool-execution path in both the replay and deferred wrappers plus the
MCP gate (the gate would have to surface the tier to the wrapper), and
composes with parallel tool calls and the deferred re-execution
semantics. That is a distinct increment from this resume-verification
change, so it gets its own ADR rather than riding this one.

## Decision

Additive to the resume-verification path.

- `ApprovalInterruption.restated_arguments: dict | None = None`: the
  arguments a human re-entered when approving an irreversible action.
- `ResumableState.approve(id, *, restated_arguments=None)`: an additive
  keyword that stamps the restatement onto the approved interruption.
  `deny` is unchanged; lower-tier approvals ignore the keyword.
- `_restate_satisfied(ai, arguments)`: a Tier 3 approval is honoured iff
  `ai.restated_arguments == arguments` (the live call arguments, which
  `_resolved_decision` already bound to `ai.arguments` via BL-193, so the
  restatement must equal both). Lower tiers are vacuously satisfied.
- Both gates enforce it: in replay (`_gate`) a missing or mismatched
  restatement on a Tier 3 approval re-pauses with a fresh interruption
  (raising `_ApprovalPause`), exactly as an undecided approval does; in
  deferred (`_deferred_gate`) it raises the framework's `ApprovalRequired`
  to re-collect the decision. A denial is unchanged (terminal in replay,
  model-visible in deferred, ADR 0027).

So a Tier 3 action runs only after a human supplies a restatement that
matches the proposed call; a typo or a stale restatement re-pauses
rather than executing.

## Scope held out of this change (BL-253)

The evidence-capture hook around an irreversible action's execution:
a workload-supplied hook invoked before and after the approved Tier 3
tool body runs, capturing pre/post state for the audit trail. It
requires the tool wrapper to know the action's tier at execution time
(a gate-to-wrapper signal across the replay, deferred, and MCP tool
paths) and a decision on how it composes with parallel tool calls, the
per-tool wall-clock accounting, and the deferred re-execution. Tracked
as `BL-253`.

## Consequences

- No L1 change, and no behavioural change below Tier 3. With no
  `TierClassifier`, or for any approval whose tier is not
  `IRREVERSIBLE`, `_restate_satisfied` is vacuously true and the resume
  path is byte-for-byte as before. The field and the `approve` keyword
  default to `None`. Nothing in the tree currently resumes *and executes*
  a Tier 3 approval, so no existing behaviour changes; the gate is purely
  additive.
- Blast radius: `harness` only. `harness/interruption.py` (the field +
  the `approve` keyword), `harness/runtime.py` (`_restate_satisfied` +
  the two gate checks). No schema change (`ApprovalInterruption` is not a
  manifest model). Rollback: revert the commit (a Tier 3 approval then
  reverts to single-step).
- Defence in depth, not replacement: restatement composes with the
  BL-193 binding (a stale approval for different arguments still fails)
  and with the deferred-mode `tool_call_approved` check; it adds the
  independent human re-entry on top.
- Tests: 10 new cases (`tests/harness/test_bl252_restate.py`) covering
  the helper and both resume paths (execute on match, re-pause on
  missing / mismatched, lower tiers unchanged).

## Revisit triggers

- `BL-253` lands the evidence-capture hook around Tier 3 execution.
- A workload wants restatement below Tier 3 (a per-tool or per-tier
  restate policy rather than the fixed Tier 3 gate): the check is one
  predicate (`_restate_satisfied`); generalising it to a configurable
  threshold mirrors `approval_tier`.
- A human-facing UI renders the restate step (it reads `tier`,
  `arguments`, and `rollback_plan` from the interruption, all already
  present after ADR 0031).
