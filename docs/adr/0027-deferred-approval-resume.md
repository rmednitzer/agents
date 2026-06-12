# ADR 0027: Deferred (non-replay) approval resume

- Status: Accepted
- Date: 2026-06-12
- Authors: rmednitzer (drafted by the 2026-06-12 Claude Code backlog session)
- Builds on: ADR 0002, ADR 0003, ADR 0007, ADR 0010 (BL-154), ADR 0013 follow-up (BL-193), ADR 0026

## Context

`BL-114` tracked the deepest known limitation of the approval flow
(`LIMITATIONS.md` L10): resuming after a human approval re-ran the
agent from the original prompt. The recorded decision was honoured
when the model re-proposed the same `(tool, arguments)` call
(BL-193), but every earlier tool call re-executed (non-idempotent
side effects fired twice), the model was not guaranteed to re-propose
the same call at all, and the replayed leg's tokens were paid again.
The item was deferred ("tracked, not rushed") until PydanticAI's
pause/resume primitive stabilised.

That condition is now met and was verified in-session against the
locked pydantic-ai 1.106: `DeferredToolRequests` /
`DeferredToolResults` are stable public API, a tool (or wrapper) can
raise `ApprovalRequired` to defer its own call, `RunContext` exposes
`tool_call_approved` on the resumed invocation, message histories
round-trip through `ModelMessagesTypeAdapter` as plain JSON, and an
end-to-end spike (pause, serialize, approve-resume, deny-resume)
behaved exactly as documented, including single execution of the
gated tool.

## Decision

### 1. An opt-in mode, not a replacement

`PydanticAIRuntime` gains `approval_mode: str = "replay"`, validated
at construction (`ValueError` on anything but `"replay"` /
`"deferred"`). Replay remains the default and is byte-identical:
the deferred machinery is reached only behind the flag (ADR 0007).
The existing adapter test suite passes unmodified.

### 2. Deferred pause: the leg completes, the history travels

In deferred mode the REQUIRE_APPROVAL branch of the gate raises
PydanticAI's `ApprovalRequired` instead of aborting the run. The
framework collects every needed approval and ends the leg with a
`DeferredToolRequests` output; the adapter translates that into the
same `ResumableState` type as before, with:

- `pending_approvals`: one `ApprovalInterruption` per requested
  approval, `id` = the run's own `tool_call_id` (a stable handle
  minted with the message history, unlike replay's per-check guard
  ids), `arguments` from `args_as_dict()`.
- `runtime_state` (new optional field, default `None` so every
  existing state and hand-built test is unchanged):
  `{"mode": "deferred", "messages": <jsonable history>}`. Opaque to
  the harness; only the producing runtime interprets it.
- `trace_id` and `input_payload` carry forward across re-pauses, so
  a multi-pause run stays one correlated conversation.

The wrapper that makes this possible prepends a `RunContext`
parameter (schema-invisible upstream) so the resumed invocation can
read `ctx.tool_call_approved`; local tools and the MCP
`process_tool_call` path share the same `_deferred_gate`, so neither
bypasses governance (the BL-001/073 invariant holds in both modes,
and the REJECT branch is the literally shared `_rejection` helper).

### 3. Resume: continuation, not replay

`run(resume=state)` in deferred mode rebuilds
`(message_history, DeferredToolResults)` from the state and invokes
the agent as a continuation. Prior tool calls live in the history and
do not re-execute (the headline regression test: a pre-pause
side-effect tool runs exactly once across pause + resume). Binding is
verified twice: the upstream maps decisions by `tool_call_id`, and
the gate re-verifies the executing call against the recorded approval
by the full `(tool, arguments)` tuple (BL-193 defence in depth),
consuming it once. A mismatched, already-consumed (retried leg), or
tampered approval re-pauses for a fresh decision instead of
executing. Resuming requires a decision for every pending approval
(the upstream needs a result per deferred call); a replay-shaped
state fails loud.

### 4. Deliberate semantic divergences (documented, opt-in)

- **Denial is model-visible, not terminal.** The caller's denial
  becomes `ToolDenied(message=reason)`: the model sees the refusal as
  a tool error and continues to a final answer. Replay mode keeps
  raising `ApprovalDenied`. This is the point of the primitive
  (graceful adaptation) and the main behavioural difference an
  opting-in caller accepts.
- **The paused leg's usage is charged.** A replay pause aborts
  mid-run with no usage to read; a deferred leg completes, so its
  tokens, steps, and cache counts are real spend and are charged at
  the pause boundary (a budget overflow there is authoritative and
  raises). BL-154 cross-leg seeding stays caller-driven and
  unchanged.
- **`stream()` always gates in replay mode.** A generator cannot
  surface a ResumableState; approval-gated tools still require
  `run()`, the same contract as before.
- **External execution requests are rejected.** Only approvals can
  defer a harness run; a `DeferredToolRequests.calls` entry raises
  `HarnessError` (no surface produces one in-tree).

## Consequences

- Additive to L1: one validated constructor keyword, one optional
  `ResumableState` field defaulting to `None`, new module-level
  helpers beside the existing ones. No existing signature or default
  behaviour changed; `_gate` is untouched except for the extracted,
  behaviour-identical `_rejection` helper.
- `LIMITATIONS.md` L10 is rewritten: the replay caveats now apply to
  the default mode only, with deferred mode as the documented
  opt-out; the non-idempotent re-execution risk disappears under
  deferred mode.
- 12 new deterministic tests; the full suite passes at 95.38 %
  coverage (gate 94 %).
- `BL-114` moves to resolved. The runbook's "tracked, not rushed"
  set shrinks to `BL-113`/`BL-138`, `BL-155`, `BL-179`.

## Revisit triggers

- The MCP deferred path is exercised through the shared gate and the
  local-tool end-to-end suite; an integration test against a real
  MCP server (needs the BL-120-style credentialed lane or an
  in-process MCP double that supports deferred calls) would close
  the remaining verification gap. Add it when either lands.
- If a caller needs replay-style terminal denial under deferred mode
  (raise instead of model-visible `ToolDenied`), add an opt-in
  (e.g. `deny_raises=True`) rather than changing the default
  documented here.
- `run_under_contract` passes ResumableState through unchanged; if
  enforcement ever grows pause-aware lifecycles, the
  `runtime_state` opacity contract (only the producing runtime
  interprets it) must be preserved.
- Upstream renames or moves `ApprovalRequired` /
  `DeferredToolRequests`: the imports are lazy and localised to the
  deferred branch, the ADR 0001 Protocol-boundary stance.
