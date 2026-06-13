# ADR 0034: Structured operational-memory journal (BL-245)

- Status: Accepted
- Date: 2026-06-13
- Authors: rmednitzer
- Builds on: ADR 0004 (memory namespace + store Protocols), ADR 0014
  (versioned / transactional stores), ADR 0024 (memory-layer drivers
  over Protocols)

## Context

The store Protocols (`memory/store.py`) give key/value plus TTL plus
namespace, with extension Protocols for batch / scan / CAS / versioned /
transactional / semantic access, and ADR 0032 added bitemporal facts.
None of them carries a *cognitive schema*: there is no first-class task
with a status FSM and a dependency edge, no open thread that can go
stale, no decision log, no categorized event timeline. The operator
gateway is the existence proof of that layer (tasks with a transition
table and an append-only log, threads with a next-action owner and a
stale-after window surfaced by a staleness query, a decision log, a
categorized timeline). BL-245 brings it in tree; the analysis flagged it
as the largest lift and "plausibly its own ADR".

## Decision

New module `memory/journal.py`: typed records plus a `Journal` driver
over a `MemoryStore`.

### Records (immutable pydantic models, copy-on-write)

- `Task`: `id`, `title`, `status` (`TaskStatus` FSM), `depends_on` (the
  dependency edge), an append-only `log` of `JournalEntry`, timestamps.
- `Thread`: `id`, `topic`, `next_action_owner`, `stale_after_seconds`,
  timestamps.
- `Decision`: `id`, `summary`, `rationale`, `related`, `recorded_at`.
- `Event`: `id`, `category`, `summary`, `occurred_at`.

### The two substantive pieces (the gateway leads with these)

- **Task FSM with an explicit transition table.** `_TASK_TRANSITIONS`
  maps each status to its permitted successors (`DONE` / `CANCELLED` are
  terminal). `transition_task` validates against the table (an illegal
  move raises `InvalidTransition` rather than corrupting state) and
  appends a `JournalEntry` to the log. `ready_tasks` is the
  dependency-edge query: a `PENDING` task whose every dependency exists
  and is `DONE` (a missing dependency is treated as unsatisfied, the
  fail-safe reading).
- **Thread stale-after staleness query.** `stale_threads(now)` returns
  threads whose idle time exceeds `stale_after_seconds`; `touch_thread`
  marks one fresh again. `Decision` and `Event` are the simpler
  append-and-list records (a decision log, a `timeline(category=)`).

### Persistence: a driver, not a store Protocol

`Journal` is bound to one `MemoryStore` and serializes each record to
JSON bytes under a `"<kind>.<id>"` key, so a single
`list_keys(prefix="<kind>.")` enumerates one record type and the records
inherit the store's namespace isolation, TTL (the namespace default on
write), optional audit, and optional encryption. This is the
`MemoryCompactor` / `TTLSweeper` precedent (ADR 0024 / BL-080): a driver
over the store Protocols, so any adapter backs it unchanged, nothing is
faked (ADR 0004). Construction-time validation (load-time, ADR 0007):
non-empty required text, a finite positive `stale_after_seconds` (the
BL-159 / BL-231 class), and timezone-aware timestamps.

## Scope held out of this change

- **Version-gated mutation.** `transition_task` and `touch_thread` are
  read-modify-write over the L1 surface, not version-gated, so two
  concurrent writers to the *same* record on a shared backend can
  lose-update. The reference is used under the single-writer-per-key
  posture the demotion paths (BL-224 / BL-225) already document; a
  version-gated transition over a `VersionedMemoryStore`, and a
  multi-key `transact` for a transition that also updates a dependent,
  are a revisit trigger (the surfaces exist, ADR 0014).
- **Cycle detection** on the dependency DAG: `ready_tasks` is sound under
  an acyclic graph; a cycle simply never becomes ready. Rejecting a
  cycle at `create_task` is a follow-up.
- **Session rehydration** (`context_pack`, BL-249) assembles open
  threads, recent decisions, and stale items from this journal; it is a
  separate item that now has its dependency satisfied.

## Consequences

- No L1 change. New module, new exports; nothing existing changes. The
  k/v store Protocols and adapters are untouched.
- Blast radius: `memory` only. `memory/journal.py` (new),
  `memory/__init__.py` (exports). No harness, runtime, or schema change
  (the records are not manifest models, so `gen_schema.py --check` is
  unaffected). Rollback: revert the commit.
- The records are immutable: a transition or touch produces a new model
  via `model_copy`, so a caller holding an old reference is never
  mutated under it.
- Tests: 18 new cases (`tests/memory/test_bl245_journal.py`);
  `memory/journal.py` at 100% line coverage.

## Revisit triggers

- A multi-writer deployment needs version-gated `transition_task` /
  `touch_thread` (read the record with `read_versioned`, commit with
  `write_versioned`, re-pause-style retry on a `None` conflict), or a
  multi-key `transact` for a transition plus a dependent update
  (ADR 0014).
- `BL-249` builds the `context_pack` session-rehydration helper on this
  journal.
- A workload needs dependency-cycle rejection or a richer DAG query
  (transitive readiness, critical path).
- A durable adapter wants a secondary index for `stale_threads` /
  `ready_tasks` rather than the reference's scan-and-filter.
