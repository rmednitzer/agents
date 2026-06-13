# ADR 0029: Graduated authority tiers on the guard (BL-242)

- Status: Accepted
- Date: 2026-06-13
- Authors: rmednitzer
- Builds on: ADR 0001, ADR 0002 (behavioural contracts), ADR 0003
  (budgets, MCP guards), ADR 0027 (BL-193 argument binding)

## Context

The Vertex MCP analysis (`docs/analysis/vertex-mcp-lessons.md`, merged
in #114) found that this repo's own thesis, defining authority
boundaries between humans, agents, and tools, had a flatter model than
the operator gateway it audited. `harness/guard.py` returns one of three
decisions (APPROVE / REJECT / REQUIRE_APPROVAL), and the only thing that
makes a tool require approval is membership in the contract's static
`approval_required` list. There is no notion of an action's *blast
radius*: a read-only status query and an irreversible data deletion are
governed by the same flat list, and a tool nobody thought to list is
approved silently.

The operator gateway keys authority to reversibility and blast radius
(read-only, reversible-low-blast, stateful-visible,
irreversible-high-blast) and treats correct tier classification as the
model's critical function, not a static allowlist. BL-242 brings that
axis in tree, additive to L1 (ADR 0007).

## Decision

New module `harness/authority.py`:

- `AuthorityTier` (an `IntEnum`, so a single comparison gates "this tier
  or above requires approval"): `OBSERVE` (0, read-only, no approval),
  `LOW` (1, reversible / low-blast, act and log), `STATEFUL` (2,
  visible impact, confirm first), `IRREVERSIBLE` (3, rollback expensive
  or impossible, confirm with parameters restated and evidence
  captured).
- `TierClassifier` Protocol: `classify(tool, arguments) -> AuthorityTier`,
  pure and total, workload-supplied (the framework binds no domain
  knowledge, ADR 0001, the `Embedder` / dispatcher stance). A
  model-driven classifier satisfies the same Protocol.
- `MappingTierClassifier`: the deterministic name-based reference, with a
  `default` (itself defaulting to `STATEFUL`) so an unlisted tool is
  treated as needing confirmation, the fail-safe "when unsure, ask"
  posture.

`HarnessToolGuard` gains an optional `tier_classifier` and an
`approval_tier` threshold (default `STATEFUL`). When a classifier is
supplied, the guard classifies each action and escalates a Tier
`approval_tier`-or-above action to REQUIRE_APPROVAL, beyond the static
`approval_required` list. `GuardResponse` gains a `tier` field that
annotates APPROVE (so a Tier 1 action can be logged or notified) and
REQUIRE_APPROVAL (so an approver sees the blast radius); it is never set
on REJECT, where classification does not run. `run_under_contract` gains
`tier_classifier` / `approval_tier` keywords that build and configure the
default guard (a classifier alone now triggers guard construction).

The tier-driven escalation lives entirely in the guard: it produces more
REQUIRE_APPROVAL decisions, which the existing runtime and resume
machinery already handle, so no change to the audited approval / resume
code (ADR 0027) was needed.

### Scope held out of this change (BL-251)

The richer Tier 2 / 3 approval *context* is a separate increment,
tracked as `BL-251`: carrying the `tier` onto the human-facing
`ApprovalInterruption` (symmetrically across the replay and deferred
paths), a `rollback_plan` field the model populates when proposing a
stateful change, an evidence-capture hook around an irreversible action,
and the two-step "restate the parameters" confirmation (which composes
with the BL-193 (tool, arguments) re-verification already shipped in
ADR 0027). These touch the approval / resume machinery and the model
contract; they deserve their own ADR rather than riding this guard-level
increment.

## Consequences

- No L1 change. With no `tier_classifier`, `GuardResponse.tier` is
  `None`, the guard's approval decision reduces to the exact prior
  `tool in approval_required` check, and every existing call site is
  byte-for-byte unchanged. `tier_classifier`, `approval_tier`, the
  `tier` field, and the new module are purely additive.
- Blast radius: `harness` only. `harness/authority.py` (new),
  `harness/guard.py` (the `tier` field and tier-driven escalation),
  `harness/enforcement.py` (the two new `run_under_contract` keywords and
  the guard construction), `harness/__init__.py` (exports). No runtime,
  memory, or schema change; `gen_schema.py --check` is unaffected
  (`AuthorityTier` is not a manifest model). Rollback: revert the commit.
- A custom `guard=` passed to `run_under_contract` owns its own policy;
  `tier_classifier` / `approval_tier` apply only to the default
  `HarnessToolGuard`.
- Tests: 12 new cases (`tests/harness/test_bl242_authority_tiers.py`);
  `harness/authority.py` and `harness/guard.py` at 100% line coverage.

## Revisit triggers

- `BL-251` lands the Tier 2 / 3 approval context (rollback plan,
  evidence capture, tier on the interruption, two-step restate).
- A model-driven `TierClassifier` is exercised against a live workload
  (couples to BL-120, the same gate as the embedder and reranker).
- A workload needs argument-aware classification (a wildcard delete is
  higher-blast than a single-key one): the `TierClassifier` Protocol
  already takes `arguments`; only the reference ignores them.
