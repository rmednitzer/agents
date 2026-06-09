# ADR 0024: Memory compaction, summarisation, and hot/cold tiering (BL-234, BL-235)

- Status: Accepted
- Date: 2026-06-09
- Authors: rmednitzer
- Builds on: ADR 0001-0023

## Context

`BL-135` named three reclamation gaps in one line: a size bound on the
sweeper, compaction/summarisation, and hot/cold tiering. The size-bound
half closed incrementally (`BL-212`-`BL-214`, `BL-224`, `BL-225`,
ADRs 0013/0020 territory), leaving the long-horizon context-engineering
half (S2 in the backlog sources): every in-tree reclamation mechanism,
lazy expiry (`BL-195`), the active sweeper (`BL-080`), and the
capacity pass (`BL-212`), reclaims space by *dropping* entries. A
long-horizon workload that wants to keep the information, not the
bytes, has no in-tree path: its keyspace either grows unbounded or
loses history.

Two capabilities close that half, tracked as `BL-234` (compaction and
summarisation: condense N entries into one) and `BL-235` (tiering:
keep a small working set hot, the long tail cold). Both follow
established repo patterns end to end:

- The driver-class precedent (`TTLSweeper`, BL-080/BL-212): helpers
  drive existing store Protocols rather than adding new store
  Protocols, so no adapter changes and nothing to fake (ADR 0004).
- The Protocol-plus-deterministic-reference cadence (`BL-110`
  `HashingEmbeddingProvider`, `BL-131` `Embedder` /
  `InMemorySemanticStore`): memory-local minimal Protocols so the
  framework binds no vendor (ADR 0001); the deterministic in-tree
  reference is a baseline, the model-quality implementation stays out
  of tree.
- Load-time configuration validation (ADR 0007): a mis-wired
  compactor or tier pair surfaces a `TypeError` / `ValueError` at
  construction, not mid-run.

The `VersionedMemoryStore` + `TransactionalMemoryStore` surface that
ADR 0014 brought to the durable adapters is exactly what makes
*atomic* compaction possible: without it, this ADR could only have
shipped the best-effort mode.

## Decision

### 1. `Summarizer` Protocol + `TruncatingSummarizer` reference (BL-234.a)

`memory.compaction.Summarizer` is a minimal runtime-checkable
Protocol: `async def summarize(self, values: list[bytes]) -> bytes`.
The contract is bytes-in/bytes-out, the store's own currency;
workloads that summarise text decode/encode at their boundary, and
`summarize([])` must return `b""`. An LLM-backed summarizer satisfies
it out of tree; memory imports no model SDK (ADR 0001, the `Embedder`
precedent: memory does not import skills, the layering stays one-way).

`TruncatingSummarizer(max_bytes, *, separator=b"\n", marker=b" ... ")`
is the deterministic, dependency-free reference: join the values, and
when the result exceeds `max_bytes`, keep the head and tail around the
elision marker so the output is exactly `max_bytes` long. The head
gets the larger half of the budget (earliest context usually carries
the task framing; the tail keeps the most recent entries). It is a
size-reduction baseline, not a semantic summarizer, exactly as
`HashingEmbeddingProvider` is a similarity baseline, not a semantic
embedder. Construction rejects `max_bytes <= len(marker)` with a
`ValueError` (ADR 0007: otherwise every over-budget input would
degenerate to a slice of the marker alone). One non-obvious guard is
pinned by a regression test: a zero tail budget must slice to `b""`,
not `joined[-0:]` (which is the whole value).

### 2. `MemoryCompactor` driver (BL-234.b)

`MemoryCompactor(store, summarizer, *, atomic=True)` folds many source
entries into one summary entry:
`compact(keys, *, target_key, ttl_seconds=None)` reads the live
sources in deduplicated input order, summarises them, writes the
summary to `target_key`, and deletes the sources. Keys are validated
up front, all-or-nothing (the `BatchMemoryStore` convention); absent
and expired sources are skipped; no live source returns `None`.

Atomic mode (the default) requires the store to implement both
`VersionedMemoryStore` (the read side needs version tokens) and
`TransactionalMemoryStore` (the commit side needs the multi-key
transaction), checked at construction with a `TypeError` naming the
missing Protocol (ADR 0007). The commit is one version-gated
`transact`: the summary write carries the target's observed token (or
None-for-absent) and every source delete carries its read token. A
source rewritten, expired, or deleted between read and commit fails
the whole transaction and `compact` returns `None`: no lost update, no
partial application, the caller re-reads and retries (the `BL-072`
best-effort give-up convention). On DynamoDB the transaction is one
`TransactWriteItems` call capped at 100 items, so one atomic compact
there is bounded at 99 live sources plus the summary write (the cap
surfaces as the ADR 0014 contract-boundary `ValueError`).

`atomic=False` is the explicit opt-in for backends without multi-key
transactions (S3): plain reads, then write-summary-then-delete-sources.
A crash in between leaves summary and sources both present
(re-compacting is safe; data is never lost), but a concurrent writer
can update a source after it was read and lose that update when the
source is deleted; the mode is documented for the
single-writer-per-key posture only (the `BL-224`/`BL-225` stance).

Rolling compaction is supported: `target_key` may itself appear in
`keys`, so the previous summary folds together with the new entries
into the same target. The target is then version-gated like every
source but excluded from the deletes.

`compact` returns a frozen `CompactionResult` (`target_key`,
`source_keys` in input order, `bytes_before`, `bytes_after`, and the
summary's new version token, `None` in best-effort mode). Audit events
need no new code: the compactor goes through the store's own
`read`/`write`/`delete`/`transact`, so a store constructed with the
`sink` / `base_event_fields` surface (BL-040) emits them already.

### 3. `TieredMemoryStore` (BL-235)

`memory.tiering.TieredMemoryStore(hot, cold, *, promote_on_read=True,
invalidate_cold_on_write=True)` composes two namespace-matched stores
behind the plain `MemoryStore` surface (the `InMemorySemanticStore`
composition pattern, BL-131). Construction rejects `hot is cold` and a
namespace `name`/`workload` mismatch with a `ValueError` (ADR 0007);
`retention_seconds` may differ per tier, short-lived hot plus
long-lived cold being the point of tiering.

Consistency posture, each choice with its failure-mode rationale:

- `read` falls through hot to cold and, by default, promotes the cold
  hit into hot. Promotion uses `compare_and_set(key, None, value)`
  when the hot tier implements `CASMemoryStore`, so a hot write that
  raced the fall-through read is never clobbered by the older cold
  value; a CAS-less hot tier degrades to a plain write under the
  single-writer posture. The promoted copy gets the hot tier's default
  TTL.
- `write` lands hot-first, then invalidates the cold copy. Hot-first
  means a crash in between leaves both copies present with the newer
  hot one shadowing on read; the invalidation prevents an older cold
  copy resurfacing after the hot copy expires.
  `invalidate_cold_on_write=False` skips the per-write cold round trip
  and accepts exactly that resurfacing window.
- `delete` removes cold-first, then hot. The reverse order would leave
  only the stale cold copy on a mid-operation failure, and a
  fall-through read would resurrect deleted data; cold-first leaves
  the hot copy live (still consistent, a retry deletes it).
- `demote(keys)` copies each live hot value to cold, then deletes the
  hot copy, version-gated when the hot tier implements
  `VersionedMemoryStore` (`delete_versioned` with the read token): a
  key rewritten between read and delete stays hot, is not counted, and
  the newer hot value shadows the stale cold copy until the next
  demotion or invalidation.
- `demote_to_capacity(max_hot_keys)` is the `evict_to_capacity` shape
  (BL-212) with demotion instead of loss. Ranking is the wrapper's own
  first-write sequence (an overwrite keeps its original slot, the
  BL-212 insertion-order semantics; LRU stays out of tree,
  `LIMITATIONS.md` L5); keys the wrapper never wrote carry the
  `BL-224`/`BL-225` legacy sentinel 0, oldest-first, ties broken
  lexicographically. Promotion stamps the key so a just-promoted entry
  is not immediately demoted as legacy-oldest. The stamp map is pruned
  against the live hot keyspace on every capacity pass, so it stays
  bounded.

Extension Protocols of the inner tiers are deliberately not forwarded:
a batch read or a transaction spanning two tiers has no single-store
semantics to inherit (ADR 0004 "don't fake it"); callers needing them
hold the inner store directly.

### 4. What this ADR deliberately does not include

- No new store Protocol and no adapter changes: both capabilities are
  drivers/compositions over `MemoryStore`, `CASMemoryStore`,
  `VersionedMemoryStore`, and `TransactionalMemoryStore` as they
  exist.
- No LRU: recency-ranked demotion would need read tracking in the
  store contract; insertion-order is the documented in-tree policy
  (BL-212) and LRU remains out of tree (`LIMITATIONS.md` L5).
- No scheduler: like `TTLSweeper.run` gave the sweeper a loop, a
  periodic compaction/demotion loop is the workload's composition
  choice (the `lifecycles` keyword, BL-104, already covers running one
  around a workload); the primitives here are one-shot and explicit.
- No model-quality summarizer: `TruncatingSummarizer` guarantees byte
  budget and determinism, nothing about meaning. The `Summarizer`
  Protocol is the extension point (the `BL-110`/`BL-111` no-vendor
  stance).

## Consequences

- `BL-135` is fully resolved: size bounds (previous waves) plus
  compaction/summarisation (`BL-234`) plus tiering (`BL-235`).
  `LIMITATIONS.md` L5 narrows again: vector retrieval closed at
  ADR 0011, compaction/tiering closed here; the remaining L5 gaps are
  a durable `SemanticMemoryStore` adapter, an LRU policy, and
  model-quality summarisation/embedding, all deliberate.
- Long-horizon workloads get an in-tree context-engineering loop:
  write turn entries, compact them rolling into a bounded summary
  (atomic on InMemory/SQLite/Redis/DynamoDB, best-effort on S3), tier
  the working set hot with the long tail cold, and bound the hot tier
  with capacity-ranked demotion instead of loss.
- All changes are additive (ADR 0007): two new modules
  (`memory/compaction.py`, `memory/tiering.py`), new exports, no L1
  import path or signature touched, no behaviour change for any
  existing call site.
- Atomic compaction inherits the ADR 0014 transaction economics: on
  DynamoDB, `TransactWriteItems` is billed at roughly double a plain
  write and capped at 100 items; a workload compacting more than 99
  sources per call batches its calls or accepts best-effort mode.
- Tests: 59 new test cases (`tests/memory/test_bl234_compaction.py`,
  29; `tests/memory/test_bl235_tiering.py`, 30) covering Protocol
  satisfaction, truncation arithmetic (including the `joined[-0:]`
  zero-tail guard), atomic commit/conflict/rolling/TTL/audit paths on
  `InMemoryStore` and `SQLiteStore`, best-effort paths, promotion/CAS
  races, demotion version-gating, capacity ranking with legacy and
  promoted keys, per-tier TTLs, and every construction-time rejection.

## Revisit triggers

- If a workload needs atomic compaction over more than 99 sources on
  DynamoDB in one shot, the path is a chunked compact-into-target
  loop (rolling compaction makes this safe); only if that proves
  insufficient consider a backend-aware partitioned transaction
  helper.
- If read-recency demotion (LRU) becomes a real need, it requires a
  read-tracking surface on the store contract and a new ADR; do not
  bolt read stamps onto the wrapper (they would not survive a process
  restart and would diverge from the BL-212 documented semantics).
- If a durable `SemanticMemoryStore` adapter lands (the remaining L5
  item), consider a tiered semantic composition (hot vectors in
  memory, cold in the durable adapter) following the same
  promotion/demotion posture.
- If PydanticAI or the provider SDKs stabilise a first-party
  summarisation/compaction primitive worth wrapping, it belongs out of
  tree behind `Summarizer` (the ADR 0001 boundary), not in memory.
