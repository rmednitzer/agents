# ADR 0037: Session rehydration over the journal (BL-249)

- Status: Accepted
- Date: 2026-06-13
- Authors: rmednitzer
- Builds on: ADR 0005 (workloads), ADR 0010 (entry point / CLI),
  ADR 0034 (the operational-memory journal)

## Context

`agents run <workload> <query>` is one-shot and synchronous with no
trigger / schedule model, and `ResumableState` resumes a single paused
approval leg, not the *operational context* (open threads, stale loops,
recent decisions) on a fresh session. The operator gateway runs
single-shot timer-scheduled agents inside a hardened envelope and offers
a session-start context refresh. BL-245 (ADR 0034) shipped the journal
those records live in; BL-249 turns it into a session-start workflow.

## Decision

Add `context_pack` to `memory.journal`: an async function that assembles
a `ContextPack` from a `Journal`, the session-start context refresh.

- `ContextPack` (frozen): `ready_tasks` and `in_progress_tasks` (what is
  actionable now), `stale_threads` split from still-fresh `open_threads`
  (what is neglected), and `recent_decisions` (the latest reasoning).
- `context_pack(journal, *, now=None, recent_decisions=5)` queries the
  journal's existing `ready_tasks` / `list_tasks` / `stale_threads` /
  `list_threads` / `decisions` and packs them. Read-only;
  `recent_decisions` is validated non-negative. The injected `now`
  (default the wall clock) drives the staleness split deterministically,
  the ADR 0034 / 0036 stance.

The **scheduled single-shot envelope** is a deployment pattern, not a
contract change (as the backlog notes). A workload rehydrates at start by
calling `context_pack` over its journal-backed namespace and feeding the
pack into its prompt, then runs one shot under `run_under_contract`
(optionally behind a timer / cron in the deployment). A reference usage:

```python
journal = Journal(store)              # store bound to the workload namespace
pack = await context_pack(journal, now=datetime.now(UTC))
# render pack.ready_tasks / pack.stale_threads / pack.recent_decisions
# into the prompt, then run one shot:
result = await run_under_contract(runtime, contract, MyInput(context=pack, ...), MyOutput)
```

A delta mode (only what changed since the last state commit) layers on
the journal's timestamps and is a follow-up.

## Scope held out of this change

- A packaged reference *workload bundle* (`workloads/<name>/` with a
  manifest and CLI) and the hardened single-shot envelope's operational
  hardening (sandbox, timeout, the scheduler integration) are a
  deployment-pattern demo and docs, not a substrate contract; the in-tree
  contract is `context_pack`. The usage above is the reference.
- The delta / since-last-commit mode (above).

## Consequences

- No L1 change. Purely additive: a new function and result type in
  `memory.journal`; nothing existing changes.
- Blast radius: `memory` only (`memory/journal.py` gains `context_pack` /
  `ContextPack`, `memory/__init__.py` exports). No harness, runtime, or
  schema change. Rollback: revert the commit.
- Builds entirely on the journal's existing read methods, so it inherits
  their namespace / TTL semantics and adds no new persistence surface.
- Tests: 7 new cases (`tests/memory/test_bl249_context_pack.py`).

## Revisit triggers

- A packaged reference workload bundle and the hardened single-shot
  deployment envelope (sandbox + timeout + scheduler).
- The delta mode (context since the last state commit), using the
  journal's `recorded_at` / `updated_at` timestamps.
- A `context_pack` variant that also folds in bitemporal facts (ADR 0032)
  for "what we currently believe" alongside the task/thread state.
