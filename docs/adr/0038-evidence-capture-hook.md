# ADR 0038: Evidence-capture hook around an irreversible action's execution (BL-253)

- Status: Accepted
- Date: 2026-06-13
- Authors: rmednitzer
- Builds on: ADR 0029 (authority tiers, BL-242), ADR 0031
  (approval-context payload, BL-251), ADR 0033 (two-step restatement,
  BL-252)

## Context

ADR 0033 (BL-252) delivered the resume-verification half of the
graduated-authority approval context (the two-step "restate the
parameters" confirmation for a Tier 3 action) and split the last named
refinement forward to BL-253: an evidence-capture hook around the
*execution* of an approved irreversible action. This ADR delivers it.

The operator gateway, on an irreversible high-blast change (a deletion, a
key rotation), captures the pre- and post-state for the audit trail, so a
reviewer can see what a file tree, a database row, or a key version
looked like before and after the action. The substrate had no seam for
this: graduated authority classifies a call's blast radius (ADR 0029),
BL-251 carries the tier and a rollback plan onto the human approval, and
BL-252 makes a Tier 3 approval require the parameters restated, but
nothing wraps the *execution* of the approved action to record evidence.

The reason it was held back (named in ADR 0033) is that the tool wrapper
runs the body, and the wrapper did not know the action's tier at
execution time: the guard gate computed the tier and returned only a
`str | None` proceed signal. So the increment needs a gate-to-wrapper
tier signal across all three tool-execution paths (replay-local,
deferred-local, MCP), plus decisions on how the hook composes with
concurrent tool calls, the per-tool wall-clock accounting, and the
deferred re-execution semantics.

## Decision

Additive to the runtime adapter; no L1 change, no Runtime Protocol
change (the hook is adapter configuration, like `model_settings` /
`approval_mode`).

- New module `harness/evidence.py`:
  - `EvidenceContext` (frozen): the call context for `before` (`tool`,
    `arguments`, `tier`, `tool_call_id`, `rollback_plan`).
  - `EvidenceHook` Protocol (`@runtime_checkable`): `before(context) ->
    token` and `after(token, *, error=None)`. Workload-supplied (the
    `TierClassifier` / `RollbackPlanner` stance, ADR 0001).
  - `RecordingEvidenceHook` + `EvidenceRecord`: the deterministic
    in-tree reference (the `Mapping*` stance), an in-memory recorder
    whose token is the index of the `before` entry.
- Gate-to-wrapper tier signal: `_gate` / `_deferred_gate` now return a
  small frozen `_GateResult(soft, tier, rollback_plan)` instead of
  `str | None`. `soft` set is the soft-reject message; `soft is None` is
  clearance to proceed, carrying the action's `tier` / `rollback_plan`
  (read off the `GuardResponse`, set on APPROVE and on the approved
  REQUIRE_APPROVAL response) to the wrapper.
- Shared `_with_evidence(hook, gate, *, tool, arguments, tool_call_id,
  run)`: for anything but an `IRREVERSIBLE` action with a hook
  configured it is `await run()` (the prior path, byte-for-byte);
  otherwise `before` runs, then the body, then `after` in a `finally`
  with the body's exception (`None` on success). The token pairs a
  `before` with its `after` so concurrent Tier 3 bodies do not interleave
  state. Used by all three execution paths (`_wrap_tool`,
  `_wrap_tool_deferred`, the MCP `_mcp_process`), so coverage is uniform.
- `PydanticAIRuntime(..., evidence_hook=None)`: an optional
  `EvidenceHook` threaded to the wrappers. `None` (the default)
  preserves L1 exactly.

Design decisions the held-out scope called out:

- **Concurrency**: the hook is paired by the opaque token `before`
  returns, not by `tool_call_id`, so parallel Tier 3 bodies each hold
  their own token in their own frame and do not cross-contaminate. A hook
  that needs the context in `after` returns it (or state derived from it)
  as the token.
- **Per-tool wall-clock**: the bracket sits inside the wrapper's per-tool
  wall-clock window, so a hook's own duration counts toward
  `max_wall_clock_seconds_per_tool` (keep a hook light, or raise the
  cap). The run-level wall-clock watchdog bounds it regardless. This is
  the simpler, lower-risk placement (the alternative, excluding the hook
  by relocating the timing, churns the tested timing code for marginal
  benefit); revisitable if a real hook proves heavy.
- **Deferred re-execution**: a Tier 3 action always routes through
  approval first (Tier 3 >= the STATEFUL default threshold), so evidence
  fires on the post-approval leg, never on a first-pass APPROVE; in
  deferred mode the resumed leg runs the body exactly once, so the hook
  fires once.

What this is not: it does not gate (the Tier 3 approval and the BL-252
restatement already did), it does not execute a rollback (that stays the
`RollbackPlanner`'s descriptive plan plus a human / workload decision),
and it captures the harness-visible call context, not the tool's return
value (post-state is the hook's own snapshot, decoupled from the tool's
output shape).

## Consequences

- No L1 change and no behavioural change without a hook. `_GateResult` is
  internal; the only external surface is the new module and the
  `evidence_hook` keyword (default `None`). A runtime without a hook, or
  any action below Tier 3, is the prior path byte-for-byte.
- Blast radius: `harness` only. New `harness/evidence.py`;
  `harness/runtime.py` (the `_GateResult` return, `_with_evidence`, the
  three wrappers, the `__init__` keyword); `harness/authority.py` doc
  fixes (the evidence hook is now BL-253 / ADR 0038, not "tracked as
  BL-252"). No schema change (no manifest model touched). Rollback:
  revert the commit (the wrappers return to the un-bracketed body and the
  gate to a `str | None` proceed signal).
- `evidence.py` and `authority.py` at 100% line coverage; aggregate
  coverage 95.77% (gate 94%). The MCP evidence path shares
  `_with_evidence` with the local paths (covered by unit and integration
  tests); the `_mcp_process` body itself stays the long-standing
  no-live-server coverage gap.
- Tests: 13 new cases (`tests/harness/test_bl253_evidence.py`): the
  reference hook and context, the `_with_evidence` helper (no-hook,
  lower-tier, none-tier, Tier 3 success, Tier 3 failure-and-reraise,
  concurrent token pairing), and end-to-end through the runtime (replay
  fires around the body with the rollback plan attached, lower tier fires
  nothing, a failed body is still recorded, deferred fires once with a
  per-call id).
- `test_runtime_adapter.py` was updated for the `_gate` return-type
  change (its `_run_gate` helper unwraps `.soft`); no behavioural test
  changed.

## Revisit triggers

- A real evidence hook proves heavy enough that counting it against the
  per-tool wall-clock is wrong: relocate the timing so the bracket sits
  outside the per-tool window (the gate-exclusion precedent),
  consolidating the three paths' timing into `_with_evidence`.
- A workload wants evidence below Tier 3 (a per-tier or per-tool policy):
  the firing condition is one comparison in `_with_evidence`, mirroring
  `approval_tier`.
- A hook needs the tool's return value (not just external pre/post
  snapshots): extend `after` with the result, or add a third phase.
- Durable, fact-keyed evidence storage tied to the BL-245 journal or a
  provenance record, rather than the in-memory reference recorder.
