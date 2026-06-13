# ADR 0031: Approval-context payload on the interruption (BL-251)

- Status: Accepted
- Date: 2026-06-13
- Authors: rmednitzer
- Builds on: ADR 0002 (behavioural contracts), ADR 0003 (budgets, MCP
  guards), ADR 0027 (BL-193 argument binding, deferred resume), ADR 0029
  (graduated authority tiers, BL-242)

## Context

ADR 0029 brought blast-radius authority tiers to the guard (BL-242): a
`TierClassifier` assigns each proposed tool call an `AuthorityTier`, and
the guard escalates a Tier-`approval_tier`-or-above action to
REQUIRE_APPROVAL. That change put the tier on `GuardResponse`, the
machine-facing object the runtime reads, but explicitly held out
surfacing it onto the human-facing `ApprovalInterruption`:

> Surfacing the tier onto the human-facing ApprovalInterruption is held
> out to BL-251.

So today an approver (or a UI rendering the pause) sees only the tool
name, the arguments, and the pending decision. They cannot see the
*blast radius* of what they are confirming, nor any proposed way to undo
it. The operator gateway the Vertex MCP analysis audited treats both as
first-class context for a Tier 2 / 3 confirmation: the tier drives the
prompt, and an irreversible action is confirmed with a rollback path in
view.

## Decision

This increment delivers the *data-carrying* half of BL-251: the approval
context that should travel with the pause. It is additive to L1
(ADR 0007) and touches no resume-verification or side-effect semantics.

### `ApprovalInterruption` gains two optional fields

- `tier: AuthorityTier | None = None`: the proposed action's blast-radius
  tier, when a `TierClassifier` is active. ADR 0029 surfaced this onto
  the guard's `GuardResponse`; this carries it through to the
  interruption.
- `rollback_plan: str | None = None`: a human-readable description of how
  the action would be undone.

Both default `None`, so a hand-built or pre-BL-251 interruption is
unchanged.

### `RollbackPlanner` Protocol + `MappingRollbackPlanner` reference

New in `harness/authority.py`, beside `TierClassifier`:
`plan(tool, arguments) -> str | None`, workload-supplied so the framework
binds no domain knowledge (the ADR 0001 `Embedder` / `TierClassifier`
injection stance). A model-driven planner (the model drafts the rollback
when it proposes a stateful change, which is how "the model populates the
plan" is realised without binding the substrate to a model) satisfies the
same Protocol; `MappingRollbackPlanner` is the deterministic name-based
reference (an unlisted tool returns `None`). The planner must be pure:
capturing evidence or executing the rollback is a separate concern
(BL-252).

### Guard and runtime wiring

- `GuardResponse` gains `rollback_plan`. `HarnessToolGuard` gains an
  optional `rollback_planner`, consulted *only* on the approval branch
  (it never changes a decision); the plan rides on the response.
- Replay path (`_gate`): `tier` and `rollback_plan` are read straight off
  the `GuardResponse` onto the `ApprovalInterruption`.
- Deferred path (ADR 0027): the response is discarded when the gate
  raises the framework's `ApprovalRequired`, so the gate records
  `(tier, rollback_plan)` in the run-scoped `_GuardState` keyed by the
  call's `tool_call_id` (the same stable id the deferred pause uses), and
  `_deferred_resumable` reads it back per approval. This makes the two
  paths symmetric, as the backlog requires.
- `run_under_contract` gains a `rollback_planner` keyword threaded into
  the default guard. A planner only annotates an approval some other rule
  already requires, so unlike `tier_classifier` it does *not* trigger
  guard construction by itself.

### Scope held out of this change (BL-252)

The *behavioural* half of BL-251 is a separate increment, tracked as
`BL-252`: an evidence-capture hook invoked around an irreversible
(Tier 3) action, and the two-step "restate the parameters" confirmation
on resume (composing with the BL-193 (tool, arguments) re-verification
already shipped in ADR 0027). Both touch the audited side-effect and
resume-verification paths and carry real semantic weight (a side effect;
a change to what counts as a valid resume), so they deserve their own ADR
rather than riding this data-only increment. This mirrors how BL-242 was
split from BL-251 and BL-247 from BL-250.

## Consequences

- No L1 change. With no `TierClassifier` and no `RollbackPlanner`, both
  new fields are `None` on every interruption and every existing call
  site is byte-for-byte unchanged. The fields, the Protocol, the guard
  and `run_under_contract` keywords are purely additive.
- Blast radius: `harness` only. `harness/authority.py` (the
  `RollbackPlanner` Protocol + `MappingRollbackPlanner`),
  `harness/interruption.py` (the two fields),
  `harness/guard.py` (`GuardResponse.rollback_plan`, the
  `rollback_planner` param, the approval-branch consult),
  `harness/runtime.py` (the replay and deferred threading, the
  `_GuardState` context map), `harness/enforcement.py` (the
  `run_under_contract` keyword), `harness/__init__.py` (exports). No
  runtime-provider, memory, or schema change: `ApprovalInterruption` is
  not a manifest model, so `gen_schema.py --check` is unaffected.
  Rollback: revert the commit.
- `AuthorityTier` is an `IntEnum`, so a serialized `ApprovalInterruption`
  carries `tier` as its integer value and round-trips back to the enum,
  keeping a persisted or transported pause state JSON-clean (the same
  property the deferred `runtime_state` relies on).
- A custom `guard=` passed to `run_under_contract` owns its own policy;
  `rollback_planner` applies only to the default `HarnessToolGuard`.
- Tests: 15 new cases
  (`tests/harness/test_bl251_approval_context.py`);
  `harness/authority.py`, `harness/guard.py`, and
  `harness/interruption.py` at 100% line coverage.

## Revisit triggers

- `BL-252` lands the behavioural half (evidence capture around a Tier 3
  action, the two-step parameter restatement on resume).
- A model-driven `RollbackPlanner` is exercised against a live workload
  (couples to BL-120, the same gate as the embedder, reranker, and a
  model-driven `TierClassifier`).
- A workload needs argument-aware rollback plans (naming the exact key a
  delete would remove): the `RollbackPlanner` Protocol already takes
  `arguments`; only the reference ignores them, exactly as for
  `TierClassifier`.
