# ADR 0032: Bitemporal fact store (BL-250)

- Status: Accepted
- Date: 2026-06-13
- Authors: rmednitzer
- Builds on: ADR 0004 (memory namespace + store Protocols), ADR 0024
  (memory-layer drivers/compositions over Protocols), ADR 0028 (BL-247,
  which shipped the demotion ranking hook and split this Protocol
  forward)

## Context

BL-247 (ADR 0028) shipped the decay-ranked demotion hook and the
`decay_strength` reference, and split the bitemporal half forward to
BL-250. The gap it named: a key/value `MemoryStore` records *what* a
value is, with no model of *when it was true* versus *when the system
learned it*. An agent reasoning about a changing world needs both axes.
"axiom's role is compute" has a *validity* interval (when it holds in the
world) independent of its *transaction* interval (when this store
believed it), and revising a belief should not erase the prior one, so
the agent can ask both "what is true now?" and "what did we believe last
week about last month?". This is the operator-gateway fact-store pattern
(bitemporal facts with a confidence and auto-supersession).

## Decision

New module `memory/bitemporal.py`:

- `BitemporalFact` (frozen dataclass): `subject`, `predicate`, `value`
  (bytes), `confidence` (in [0, 1]), the validity axis
  (`valid_from` / `valid_to`, `None` = open-ended), the transaction axis
  (`recorded_at` / `superseded_at`, `None` = still believed),
  `superseded_by` (the id of the replacing record), and a deterministic
  `fact_id`.
- `BitemporalMemoryStore` Protocol, addressed by `(subject, predicate)`:
  - `record(...)` appends a new belief and *auto-supersedes* the prior
    current belief for the same `(subject, predicate)` (sets its
    `superseded_at` / `superseded_by`), atomically with the append.
  - `current(subject, predicate, *, now=...)` is the live point query:
    the non-superseded fact whose validity window covers `now`.
  - `as_of(subject, predicate, *, valid_at, known_at=...)` is the full
    bitemporal point query: the belief held at transaction-time
    `known_at` whose validity covers `valid_at`.
  - `history(subject, predicate)` is the append-only record sequence.
- `InMemoryBitemporalStore`: the in-process reference (the BL-072 /
  BL-124 "Protocol plus reference first" cadence), `asyncio.Lock`
  serialized. Construction-time validation (load-time, ADR 0007):
  timezone-aware datetimes only (so the two axes never raise on a
  naive-vs-aware comparison), `confidence` finite and in [0, 1] (the
  BL-159 / BL-231 finite class), a positive validity interval, and
  `subject` / `predicate` validated as memory keys (`validate_key`).

### Why a standalone Protocol, not a `MemoryStore` extension

The L2 extensions (`Batch` / `Scan` / `ContentAddressable` / `CAS` /
`Versioned` / `Semantic`, `memory/store.py`) all add operations to the
*same* key-addressed model, so they extend `MemoryStore`. A bitemporal
fact is addressed by `(subject, predicate)` plus two time axes, a
different data model, so this is a sibling Protocol in the memory package
rather than a `MemoryStore` extension. This is the stance ADR 0024 took
for the `MemoryCompactor` driver and the `TieredMemoryStore`
composition: a memory-layer construct that does not pretend to be the
k/v Protocol. The name keeps the BL-247 / ADR 0028 label for
traceability.

## Scope held out of this change

- Durable adapters (SQLite / Redis / DynamoDB / S3): the
  Protocol-plus-reference cadence ships the in-memory reference first;
  durable backends are a follow-up when a workload needs persistence.
- The optional `MemoryRead` / `MemoryWrite` audit surface is keyed by a
  single key and stays with the k/v adapters; a fact-keyed audit travels
  with the structured-journal layer (BL-245).
- Confidence-threshold pruning and decay-weighted fact ranking compose
  the existing `decay_strength` (ADR 0028) with this store; out of tree
  until a workload needs them.

## Consequences

- No L1 change. New module, new exports (`BitemporalFact`,
  `BitemporalMemoryStore`, `InMemoryBitemporalStore`); nothing existing
  changes. The k/v `MemoryStore` Protocols and adapters are untouched.
- Blast radius: `memory` only. `memory/bitemporal.py` (new),
  `memory/__init__.py` (exports). No harness, runtime, or schema change
  (`BitemporalFact` is not a manifest model, so `gen_schema.py --check`
  is unaffected). Rollback: revert the commit.
- Supersession semantics: once a fact is superseded, `current` no longer
  returns it even for a time inside its validity window (we no longer
  believe it); `as_of` with a `known_at` before the supersession
  recovers it. This is the intended bitemporal distinction (the two axes
  are independent).
- Tests: 16 new cases (`tests/memory/test_bl250_bitemporal.py`);
  `memory/bitemporal.py` at 100% line coverage.

## Revisit triggers

- A workload needs a durable bitemporal adapter (then mirror the
  reference onto SQLite / Redis / DynamoDB, documenting each backend's
  supersession atomicity, the ADR 0014 cadence).
- The structured-journal layer (BL-245) builds on this store and wants a
  fact-keyed audit surface.
- A workload needs confidence-threshold pruning or decay-weighted fact
  ranking (compose `decay_strength`, ADR 0028).
