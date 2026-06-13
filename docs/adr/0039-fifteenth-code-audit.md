# ADR 0039: Fifteenth code audit (BL-254 .. BL-262)

- Status: Accepted
- Date: 2026-06-13
- Authors: rmednitzer
- Builds on: ADR 0009 / 0011 / 0013 / 0015 / 0017 / 0018 / 0019 / 0020 /
  0021 / 0022 / 0023 (the prior audit waves) and ADR 0025 (the
  fourteenth, full-pass audit)

## Context

A comprehensive adversarial audit triggered by the maintainer, the first
full pass since the fourteenth audit (ADR 0025, 2026-06-12). Since then a
large body of code landed that no audit had covered: the deferred-resume
and prompt-cache paths in `harness/runtime.py` (ADR 0026 / 0027) and the
entire `authority`, `grounding`, `bitemporal`, `journal`, `fallback`,
`freshness`, `evidence`, and `retrieval` modules (ADR 0028 .. 0038). The
class-extension principle (runbook 2.3) says the next finding is usually a
known fault class re-applied to that new surface.

Method: five parallel adversarial reviewers partitioned by area (harness
execution core; harness peripherals; memory core + adapters; memory
composition + crypto/FSM; skills / workloads / evaluation / CLI), each
hunting concrete instances of the repo's tracked fault classes, plus an
independent baseline (every declared gate) and trusted-source validation
of the highest-risk external contracts. Every reviewer claim was
re-verified against the source before acceptance; two High-severity
claims did not survive verification and are recorded below as
non-findings.

Baseline (entry and exit identical, all green): 1370 -> 1390 tests,
coverage 95.77% -> 95.76% (gate 94%), ruff + mypy(strict) clean,
pip-audit `--strict` with zero suppressions clean, `uv lock --check`
fresh, gitleaks clean (60 commits), schema no drift, eval P@1 = MRR = 1.0.
Validated against trusted sources: the pydantic-ai 1.106 deferred-tools
contract (prior calls not re-executed on resume, the `approvals` map
shape, `ApprovalRequired` -> `ToolCallPart`, `RunContext.tool_call_approved`),
the `RunUsage.cache_read_tokens` / `cache_write_tokens` attribute names
(so the cache surfacing is live), and the AES-GCM construction in
`memory/encryption.py` (96-bit `os.urandom` nonce, `namespace::key` AAD,
authenticated legacy fallback).

No Critical or confirmed-exploitable High finding. The findings are one
security-hardening item and a cluster of Low / Low-Medium correctness
items, several of them new instances of fault classes the repo already
validates elsewhere.

## Decision

Each fix is additive (a strict narrowing or a pure improvement, prior
behaviour preserved except where it was incorrect) with a boundary-named
regression test.

### BL-254 [Medium] Hardlink bypass of the symlink defense (`skills/sources.py`)

`LocalSkillSource.fetch` refuses a symlink in the source tree so a
crafted local mirror cannot copy a secret's contents into the installed
bundle, but a hardlink achieves the identical exfiltration (a second
directory entry for an inode that may live outside the source tree) while
reading as a regular file under `is_symlink()` / `is_file()`. Fix: refuse
any regular file with `st_nlink > 1`, the conservative stance matching the
symlink refusal. The BL-169 / BL-172 / BL-190 link-on-install class.
Test: `tests/skills/test_bl254_hardlink_source.py` (refuses a hardlink to
an external file; still accepts an ordinary single-link file).

### BL-255 [Low-Med] `grounding_predicate` builds the full list instead of short-circuiting (`harness/grounding.py`)

The SOFT postcondition, evaluated over untrusted model output, built the
complete ungrounded-citation list and then negated it, so a confabulated
claim with many ungrounded tokens forced O(all-tokens) work (each token an
O(|sources|) substring scan). Fix: a `_any_ungrounded` boolean helper that
short-circuits on the first ungrounded token, used by `grounding_predicate`;
`_ungrounded` / `ungrounded_citations` stay the diagnostic list builder.
The BL-159 / BL-173 / BL-182 / BL-191 bounded-work-on-adversarial-input
class. Test: `tests/harness/test_bl255_256_261_audit15.py` (flags an
ungrounded citation and agrees with the diagnostic list; passes when all
grounded; vacuous on no matches).

### BL-256 [Low-Med] `DriftMonitor.record` accepts a non-finite count (`harness/drift.py`)

`record(predicate, category, n)` had no validation on `n`; a `NaN` / `+inf`
poisons the accumulated distribution (`math.fsum` returns `NaN`, every
ordered comparison against it is False), so `drift()` returns a garbage
signal. Fix: reject `not math.isfinite(n) or n < 0` at the input boundary,
the dual of BL-231 / BL-232. Test: same file (rejects NaN / +-inf /
negative; accepts finite non-negative including a zero no-op).

### BL-257 [Low-Med] `fuse_rrf` rank-gap on intra-list duplicates (`memory/retrieval.py`)

`enumerate(ranking)` advanced the rank counter even for duplicate ids
skipped by the dedupe guard, so a unique id following a duplicate was
scored at a penalised `1 / (k + raw_index)` instead of `1 / (k + distinct_rank)`.
In-tree callers (`query_hybrid`) feed duplicate-free rankings, but the
public `fuse_rrf` is wrong for an external ranked list with repeats (a
BM25 engine returning duplicate hits). Fix: a separate `effective_rank`
counter incremented only when an id is added. Test:
`tests/memory/test_bl257_258_audit15.py` (a duplicate does not penalise the
later unique rank; the no-duplicate case is unchanged).

### BL-258 [Low-Med] `query_hybrid` passes non-finite reranker scores to sort (`memory/semantic.py`)

A workload-injected `Reranker` returning a `NaN` score gave an undefined
sort order (every ordered comparison with NaN is False) and propagated NaN
into `HybridHit.score`. `tiering.demote_to_capacity` already guards the
identical injected-callable pattern with `math.isfinite`. Fix: validate
the reranker scores finite after the length check, raising `ValueError`.
The BL-159 / BL-221 non-finite class. Test: same file (a NaN reranker is
rejected).

### BL-259 [Low-Med] SQLite TTL sampled before `BEGIN IMMEDIATE` (`memory/sqlite.py`)

`transact` / `compare_and_set` / `write_versioned` / `mset` computed
`expires_at = now + ttl` from a `now` sampled before `BEGIN IMMEDIATE`,
while `evict_to_capacity` deliberately samples after it (with a comment).
Under cross-instance write contention (two `SQLiteStore`s on one file, a
supported WAL scenario) `BEGIN IMMEDIATE` can block up to the busy
timeout, so a stale `now` could write a short-TTL row already-expired.
Single-instance use is immune via the asyncio lock. Fix: sample `now` /
`expires_at` after `BEGIN IMMEDIATE` in all four transactional paths,
matching `evict_to_capacity`. The read-vs-CAS expiry-boundary class
(BL-157 / BL-168 / BL-177 / BL-188). Test:
`tests/memory/test_bl259_sqlite_ttl_clock.py` (a connection wrapper
advances a fake clock at `BEGIN IMMEDIATE`; the stored `expires_at`
reflects the post-BEGIN time for transact and compare_and_set).

### BL-260 [Low] S3 `list_keys` / `scan` use `GetObject` instead of `HeadObject` (`memory/s3.py`)

`_all_live_keys` and `_scan_sync` called `_get_live` (a full `GetObject`)
per listed object just to read the `expires-at` metadata and discard the
body, while the sweep paths use `_head_metadata` (`HeadObject`). Wasted
body bandwidth / latency / cost on a paid API; correctness unaffected.
Fix: a `_head_live` metadata-only helper used by both listing paths (an
expired object is excluded but not lazily deleted, so the listing stays a
pure read and avoids a per-item DELETE in the scan loop, the BL-233
containment concern; the read and sweep paths own reclamation). Test:
`tests/memory/test_bl260_s3_list_head.py` (patches `_get_live` to raise;
listing must succeed; an expired key is excluded via HEAD).

### BL-261 [Low] Evidence-hook `before()`-failure contract (`harness/evidence.py`, `harness/runtime.py`)

`_with_evidence` calls `before()` outside the `try`, so a `before()` that
raises skips `after()`; the docstrings claimed "after always runs". The
behaviour is fail-safe (a failed pre-state capture aborts the Tier 3
action before it runs, and there is no completed action to record an after
for), so the fix is contract precision, not a control-flow change: the
docstrings now state that `after` runs once `before` has returned, that a
failing `before` aborts the action and skips `after`, and that a `before`
writing external state should capture atomically. Test: same harness file
(a hook whose `before` raises aborts the action and does not call
`after`). The fifteenth-audit catch on code added in this same session
(ADR 0038).

### BL-262 [Low] `BoundedRedisStore.write` non-atomic data + index (`memory/redis.py`)

`write` set the data key (`super().write`), then ZADDed the index in a
separate round trip, so a crash between left a data key with no index
entry: an orphan invisible to `evict_to_capacity`'s ordering, so the
namespace could silently exceed `max_keys` for that key. Crash-safety is
not a documented `MemoryStore` contract, so this is defense-in-depth. Fix:
allocate the monotonic score first (a burned counter value is harmless,
scores need only be monotonic), then SET and ZADD in one MULTI/EXEC.
Test: `tests/memory/test_bl262_redis_atomic_write.py` (data + TTL + FIFO
eviction ordering preserved through the transactional write).

## Non-findings (reviewer claims rejected after verification)

- **SQLite `now`-outside-lock in `list_keys` / `scan` as a BL-188
  boundary bug** (claimed Medium): benign. The listing filters an
  already-fetched snapshot with a *fresher* `now`, correctly excluding
  keys that expired during the call; the list-then-read race is inherent
  to lazy TTL across separate calls and identical in `InMemoryStore`.
- **Concurrent approval "executes without confirmation"** (claimed High):
  false. In replay mode the run re-executes from the prompt and each
  tool's gate independently re-verifies its own approval via
  `_resolved_decision` + `_restate_satisfied` at execution, so a
  scalar-`state.pause` overwrite only re-surfaces the other approval on
  the next resume cycle (no unapproved Tier 3 execution); deferred mode
  records every concurrent approval under its `tool_call_id` in
  `approval_context` and `_deferred_resumable` iterates all of them. The
  replay-mode serial-surfacing of multiple parallel approvals is a UX
  limitation tracked below, not a security defect.

## Consequences

- Additive to L1 (ADR 0007): every fix is a strict narrowing (BL-254 /
  BL-256 / BL-258 reject an input previously mis-accepted), an internal
  efficiency or atomicity change with identical observable behaviour
  (BL-255 / BL-259 / BL-260 / BL-262), a public-API correctness fix on a
  path in-tree callers do not exercise (BL-257), or a docstring-only
  contract clarification (BL-261). No L1 import path or signature changed;
  no manifest model, so no schema drift.
- Blast radius: `skills/sources.py`, `harness/{grounding,drift,evidence,runtime}.py`,
  `memory/{retrieval,semantic,sqlite,s3,redis}.py`. Rollback: revert the
  commit; each fix is independent.
- 20 new regression tests across the six files named above; `make check`
  1390 passing, aggregate coverage 95.76% (gate 94%), ruff / format /
  mypy / pip-audit / gitleaks / eval all green.

## Revisit triggers

- **Replay-mode concurrent approvals** surface one per resume cycle
  (scalar `state.pause` keeps the last). Not a security issue, but a
  list-valued pause would let a human approve all parallel
  approval-required tools at once; weigh against the documented replay
  re-execution caveat (L10), for which deferred mode is the answer.
- **`mset` / `compare_and_set` / `write_versioned` / `transact` index
  updates on `BoundedRedisStore`** share the SET-then-ZADD shape BL-262
  made atomic for `write`; the same defense-in-depth could extend to them
  if crash-safety becomes a contract concern (it is not today).
- **ReDoS on a caller-supplied grounding `pattern`** (a developer-trusted
  pattern over untrusted `claim`): the injection stance leaves pattern
  quality to the workload, as with the embedder / classifier; documented,
  not guarded.
- **`require_fresh` clock injection** is validated only at evaluation
  time; a build-time `clock()` probe would surface a naive-returning
  clock at load (ADR 0007), at the cost of one extra call at construction.
- **`MultiSink` per-sink failures stay silent** (the BL-223 containment
  design); the runbook 7.4 candidate #5 observability gap is unchanged.
- **AES-GCM random 96-bit nonce** carries the standard ~2^32-writes-per-key
  birthday bound; rotate (the `VersionedKeyProvider` path) before then for
  a very-high-volume single-key store.
