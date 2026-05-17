# ADR 0011: Third code audit, L3 capability wave

- Status: Accepted
- Date: 2026-05-17
- Authors: rmednitzer
- Builds on: ADR 0001-0010

## Context

ADR 0009 and ADR 0010 each ran a full audit and fixed the clear bugs;
ADR 0010 wired the L2 primitives into the default path and closed the
well-scoped Tier 1/2 items. This ADR records the next increment: a
third in-depth audit that surfaced bugs the prior two passes and the
green gates did not catch, and the highest-leverage remaining pending
backlog items (`BL-111`, `BL-122`, `BL-124`, `BL-130`, `BL-131`).
Per-item state is in `docs/backlog.md`; residual risk is in
`LIMITATIONS.md`; this ADR is the why.

## Decision

### 1. A third audit; fix the clear bugs additively

A fresh deep read (the bugs the ADR 0009/0010 passes and the gates did
not catch) found and fixed, each with regression tests, no L1 signature
removed, defaults preserving prior behaviour:

- A pre-existing `dest/<name>` *symlink* let `GitHubSkillSource` /
  `MarketplaceSkillSource` escape the install directory: the code
  resolved the path before clearing it, so `resolve()` followed the
  link and extraction wrote members fully outside `dest`. This is the
  network-source twin of the `BL-169` `LocalSkillSource` hole; the fix
  there was not propagated. One hardened `_prepare_install_dir` unlinks
  the link itself before resolving and asserts containment (`BL-172`).
- `_balanced_spans` recorded the matched substring on *every* nested
  `]`, so a nested `[[[...]]]` blob from an untrusted model/MCP tool
  was O(n^2) in time and memory (a decompression-bomb analogue). It
  now records an O(1) `(open, close)` index pair per close and
  `first_json_array` slices at most a capped number of candidates
  lazily, in opening order, so the legitimate top-level array (always
  the first span) is unaffected while the bomb degrades to the
  documented parse-error fallback. `RecursionError` from a
  pathologically deep span is caught in the extractor and at the
  LLM / skill-based dispatcher boundary, keeping the DispatchError
  contract (`BL-173`).
- `compose_contracts` governance was a *first-occurrence* union, so a
  workload's SOFT `delete_guard` declared before a skill's same-named
  HARD veto silently downgraded the veto to SOFT. Governance is the
  most safety-critical set; it now keeps the strictest instance, the
  exact governance analogue of the `BL-166` pre/inv/post fix
  (`BL-174`).
- A postcondition retry directive (`BL-102`) re-ran the postcondition
  loop and re-recorded every predicate into the `DriftMonitor`,
  inflating the JSD distribution. Postcondition drift is now recorded
  exactly once per run (the final, non-retried leg) (`BL-175`).
- `DynamoDBStore.compare_and_set` (match branch) and
  `compare_and_delete` gated on `exp > :now` while `_live_item` treats
  a row as expired only when `now > exp` (live while `now <= exp`), so
  a row at the exact expiry instant was readable but CAS-absent. The
  conditions now use `exp >= :now`, the read-vs-CAS boundary class
  `BL-157` / `BL-168` fixed for the other paths (`BL-177`).
- `SQLiteStore.mset` / `mdelete` of an empty batch still ran
  `BEGIN IMMEDIATE` ... `COMMIT`, taking the database write lock to do
  nothing; an empty batch is now an early no-op (`BL-178`).

`run_under_contract` also gained an optional `parent_span_id` so a
workload that runs a contract inside another produces a correlated
span tree instead of flat siblings; None preserves the prior behaviour
(`BL-176`).

The `RetryPolicy` cross-attempt budget claim was too strong: wall-clock
is bounded end to end and tool-calls are fed live from the gate, but
token and step usage is charged from the *final* attempt's
`result.usage` only (PydanticAI raises without exposing partial usage
on a failed `agent.run()`). The docstring is corrected and the gap is
tracked (`BL-179`, `LIMITATIONS.md` L15): closing it needs upstream
partial-usage on the exception path, the same upstream-dependent shape
as `BL-114` / `BL-132`, so it is tracked, not faked.

### 2. The highest-leverage pending capabilities, now

Each is additive (a new optional parameter, a new module, or a new
side-by-side extension Protocol; ADR 0004 "don't fake it"):

- `BL-111`: `EnvKeyProvider` / `FileKeyProvider` (single key,
  base64/hex/raw, stdlib only) and the `VersionedKeyProvider` Protocol
  with the `RotatingKeyProvider` reference. `EncryptedStore` writes a
  rotation-safe key-id value envelope over a versioned provider so a
  rotation does not strand prior ciphertext; over a plain
  `KeyProvider` it keeps the exact prior on-disk format (byte-additive,
  existing encrypted data unaffected). A KMS-backed provider stays
  out-of-tree by the ADR 0001 no-vendor-binding stance (the
  `HashingEmbeddingProvider` precedent, `BL-110`); the Protocol is the
  in-tree extension point.
- `BL-122`: `AttributeACL` / `AttributeRule` (attribute-based access,
  decided per call from principal attributes, not a static role
  table), and a `harness.events.AccessDenied` event emitted by
  `ACLStore` / `wrap_acl` before raising when the optional
  `sink` / `base_event_fields` audit surface is supplied (the `BL-040`
  convention: silent without base fields). It is exported as
  `AccessDeniedEvent` to disambiguate from the `memory.errors`
  exception, mirroring `ApprovalDenied` / `ApprovalDeniedEvent`.
- `BL-124`: the `VersionedMemoryStore` extension Protocol (MVCC via a
  content-hash version token, path-independent so any write changes
  it) with `InMemoryStore` and `SQLiteStore` reference impls. Scope
  clarification: the version-token surface shipped here; multi-key
  transactions where the backend supports them, and the non-content
  backends, are the documented remainder (`BL-180`), the same
  Protocol-plus-reference-first scoping `BL-072` used. Not forwarded
  through `EncryptedStore` for the same per-write-nonce reason CAS is
  not.
- `BL-131`: the `SemanticMemoryStore` extension Protocol (vector write
  + similarity query) and `InMemorySemanticStore`, a deterministic
  in-tree reference reusing the `BL-110` `HashingEmbeddingProvider`
  through memory's own minimal `Embedder` Protocol (memory does not
  import skills; the layering stays one-way). Closes the
  vector-retrieval half of `LIMITATIONS.md` L5; compaction / tiering
  stays `BL-135`.
- `BL-130`: a new top-level `evaluation/` component plus the
  `scripts/eval.py` gate and a blocking CI `evaluation` job in the
  `ci-success` aggregate. `evaluate_dispatch` (P@1 / MRR over a JSON
  golden set) and `evaluate_trajectory` (expected vs actual contract
  terminal outcome) are deterministic and network-free with the
  `KeywordDispatcher` / a stub runtime, so behaviour regressions become
  a number that moves (`LIMITATIONS.md` L6). The harness also runs on
  an LLM dispatcher or a live runtime, gated to skip without
  credentials, for when `BL-120` lands.

### 3. ADRs are immutable; errata forward

No prior ADR is edited. ADR 0010 section 6 settled the dispatcher
count; this ADR adds the `evaluation/` component to the layout (a
seventh top-level package). The live docs (`README.md`, `CLAUDE.md`,
component READMEs) carry the forward-consistent description.

## Consequences

Positive: the exploitable skill-install symlink escape, the
untrusted-output O(n^2) / RecursionError paths, and the silent
governance downgrade are closed without an L1 break; key rotation,
attribute-based access with audited denial, MVCC version tokens, and
in-tree semantic retrieval are available; behaviour is now gated in CI.

Negative: `run_under_contract` gains one more optional parameter
(mitigated: defaults to the prior behaviour). The `evaluation`
component widens the mypy and coverage targets (combined coverage
stays above the 94% gate). The `_balanced_spans` candidate cap means a
pathological deeply-nested-prefix blob degrades to the parse-error
fallback rather than recovering a much-later array (documented; it is
the DoS bound, and a real top-level array is always candidate one).

Neutral: one ADR for the increment, as in ADR 0007-0010. Per-item
rationale stays in `docs/backlog.md` and docstrings.

## Revisit triggers

- PydanticAI exposes partial usage on a failed run: close `BL-179`
  (count failed-attempt tokens/steps against the budget).
- A workload needs multi-key transactional memory: promote `BL-180`.
- A live-model workload lands (`BL-120`): extend the evaluation harness
  with a credentialed trajectory suite, gated to skip without keys.
- An L1 Protocol must change to land an item: write a dedicated ADR,
  do not fold it here.
