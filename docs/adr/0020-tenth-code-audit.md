# ADR 0020: Tenth code audit, additive hardening

- Status: Accepted
- Date: 2026-05-26
- Authors: rmednitzer
- Builds on: ADR 0001-0019

## Context

A tenth in-depth audit of `harness`, `memory`, `skills`, `workloads`,
the CLI, `evaluation`, and the offline gates, run against the same
green gates (ruff, ruff format, mypy strict, pytest at
`cov-fail-under=94`, schema-drift, REUSE 3.x, `pip-audit`, the
dispatch evaluation gate at P@1 = MRR = 1.0). The prior audits (ADR
0009 / 0010 / 0011 / 0013 / 0015 / 0017 / 0018 / 0019) and the
BL-180 / BL-133 / BL-212-BL-214 / BL-224 / BL-225 capability waves
closed a wide surface. This audit re-walked the same *classes* the
prior audits fixed pointwise, with particular attention to the
just-merged BL-225 (`BoundedS3Store`) and whether the same
"untrusted-input must not crash" and "audit-vs-raise parity"
invariants generalise to the new metadata-read and per-key delete
surfaces.

The audit found two new previously-untracked issues in `memory/s3.py`:
the metadata-read parsing on the cold-storage adapter (both parent
`S3Store` and the new `BoundedS3Store` subclass) raised on adversarial
strings and silently accepted NaN / +inf / -inf (BL-159 / BL-205 /
BL-221 class extended to the metadata trust boundary), and the
`BoundedS3Store.evict_to_capacity` per-key delete loop had no
exception containment so a single failing DELETE crashed the audit-
emit loop before any successful key was audited (BL-202 / BL-167
class extended to the new sequential-DELETE fan-out introduced by
BL-225). Both are fixed additively with regression tests in the same
increment; the backlog tracks the line items (`BL-226`, `BL-227`).

The recurring lesson: every capability wave that adds an untrusted-
input boundary or a fan-out path is a re-instance of an invariant a
prior audit generalised on a neighbouring surface. The audit's job is
to verify the class generalises to every new boundary, not only the
most recently fixed one.

## Decision

### 1. S3 metadata trust-boundary parsing (BL-226)

A BL-159 / BL-205 / BL-221 NaN-bypass class extension on the S3
user-metadata read boundary, plus a BL-201 / BL-215 / BL-217
unparseable-input class extension on the same boundary.

`S3Store._get_live`, `S3Store._sweep_sync`, and
`BoundedS3Store._collect_live_sync` parsed `expires-at` and
`insertion-order` user-metadata strings via bare
`float(exp) if exp is not None else None` and `int(seq_raw) if
seq_raw is not None else 0`. Two distinct failure modes:

- **Non-finite floats**: `float("nan")` / `float("inf")` /
  `float("-inf")` succeed and propagate. Then `is_expired(now, NaN)`
  evaluates `now > NaN`, which is always `False` in IEEE 754, so the
  object is *never* expired by lazy read, never swept, and (in the
  bounded subclass) never excluded from the live count for the
  capacity pass either. A corrupted or hand-written
  `x-amz-meta-expires-at = "nan"` permanently masks an object from
  every expiry path. This is exactly the BL-159 cosine-similarity NaN
  bypass and the BL-205 MultiDispatcher-weight NaN bypass and the
  BL-221 BudgetTracker NaN bypass, generalised to the metadata-read
  boundary.

- **Unparseable strings**: `float("not-a-number")` and
  `int("not-a-number")` raise `ValueError`. The whole read / sweep /
  eviction scan crashes past the documented `S3Store` exception
  contract on the first corrupted object. A single bad entry takes
  out the entire keyspace's `sweep_expired` or `evict_to_capacity`
  call. This is the BL-201 (OpenAI `_decode_lines` malformed-row
  resilience) / BL-215 (SKILL.md UTF-8 decode boundary) / BL-217
  (subprocess metadata-frame structural validation) class of
  external-input-must-not-crash invariant, generalised to the same
  metadata-read boundary.

The fix is two helpers in `memory/s3.py`:

- `_safe_float(v: str | None) -> float | None`: returns `None` if the
  value is missing, not parseable as a float, or not finite
  (`math.isfinite` rejects NaN / +inf / -inf). The non-finite
  rejection is the BL-159 / BL-205 / BL-221 guard at this boundary;
  the `ValueError` swallow is the BL-201 / BL-215 / BL-217 guard.
- `_safe_int(v: str | None) -> int`: returns `0` if missing or
  unparseable. Zero matches the BL-225 legacy-migration default (an
  object written by a bare `S3Store` has no `insertion-order` and
  evicts first), so a corrupted value falls back to "this is an old
  entry" -- the *most defensive* eviction-order default.

Both helpers are applied at every metadata-read site in `memory/s3.py`
(three call sites: `_get_live`, `_sweep_sync`,
`_collect_live_sync`). The choice between "corrupted = expired" vs
"corrupted = no-TTL" went with **no-TTL** (the entry stays live) by
the cold-storage / audit-pack stance: losing data because a metadata
attribute was malformed is worse than keeping a corrupted-TTL entry
around. An operator can re-write the entry with valid metadata to
restore the TTL.

### 2. Evict-to-capacity per-key delete containment (BL-227)

A BL-202 / BL-167 audit-vs-raise parity class extension on the new
BL-225 sequential-DELETE fan-out, plus a BL-222 / BL-223 per-item
failure containment dual.

`BoundedS3Store.evict_to_capacity` ran the per-key `delete_object`
loop inside an `asyncio.to_thread` with no exception containment:

```python
def _delete_all() -> None:
    for k in to_evict:
        self._s3.delete_object(Bucket=self._bucket, Key=self._okey(k))

await asyncio.to_thread(_delete_all)
for k in to_evict:
    self._audit.delete(k, existed=True)
return len(to_evict)
```

If a single DELETE raised (S3 throttle, transient access drift,
network blip), the exception propagated out of `asyncio.to_thread`
and the audit loop below was never reached: state mutation (the keys
deleted before the failure) with no audit emitted at all. This is
the exact audit-vs-raise parity violation BL-202
(`emit_wall_clock_exceeded` on the budget-watchdog boundary) and
BL-167 (`MemoryAudit` reserved-key construction-time check) closed
on neighbouring surfaces.

Separately, the loop crashed the *whole* eviction on the *first*
failure, so a transient error on one key cancelled every later
delete. This is the BL-222 (`MultiDispatcher` per-member failure
containment) / BL-223 (`MultiSink` per-sink failure containment)
class on the sequential-DELETE fan-out side.

The fix contains per-key failures and emits audit only for
actually-deleted keys:

```python
def _delete_all() -> list[str]:
    deleted: list[str] = []
    for k in to_evict:
        try:
            self._s3.delete_object(Bucket=self._bucket, Key=self._okey(k))
            deleted.append(k)
        except Exception:
            continue
    return deleted

actually_deleted = await asyncio.to_thread(_delete_all)
for k in actually_deleted:
    self._audit.delete(k, existed=True)
return len(actually_deleted)
```

`BaseException` (`KeyboardInterrupt`, `SystemExit`,
`asyncio.CancelledError`) is deliberately *not* contained, parity
with the BL-165 / BL-223 invariant: terminal signals propagate; only
operational `Exception` types are contained for the per-key fan-out
robustness.

The failed key remains alive after the call returns. The next
`TTLSweeper` cycle calls `evict_to_capacity` again and retries the
failed key, which is the BL-199 sweeper-resilience contract
(transient backend errors self-heal on the next interval) already
applied to the age-only sweep path. The function returns the count
of *actual* deletions, not the count of *attempted* deletions, so an
operator monitoring `TTLSweeper.evicted_total` sees a truthful
keyspace shrinkage.

## Consequences

- The fixes are additive to the L1 Protocols (ADR 0007). No public
  signature changed; no caller behaviour changed on the happy
  path. The exception types still propagating on the fan-out
  boundary (`BaseException`) are existing types, not new ones.
- 33 new regression tests (`tests/memory/test_bl226_bl227_audit10.py`):
  - 13 parametrized `_safe_float` cases (None, empty, unparseable,
    NaN, NaN with mixed case, +inf, -inf, Infinity, plus valid
    floats including 0, negative, large exponents).
  - 9 parametrized `_safe_int` cases (None, empty, unparseable, a
    float string `"1.5"` that is not a valid int, NaN, inf, plus
    valid ints including 0, negative, a large integer).
  - 3 BL-226 cases on the parent `S3Store` (corrupt `expires-at`
    does not crash `read` or `sweep_expired`; NaN `expires-at` does
    not silently mask from expiry).
  - 3 BL-226 cases on `BoundedS3Store` (corrupt `insertion-order`
    does not crash `evict_to_capacity`; corrupt `expires-at` does
    not crash the evict path; NaN `insertion-order` is treated as
    legacy seq=0).
  - 4 BL-227 cases (partial-failure audits only successes; all-fail
    emits no audit and returns 0; happy path unchanged; `SystemExit`
    still propagates).
- Coverage stays above the 94% gate (the absolute number fluctuates
  with pytest discovery and the new tests; the gate is what
  matters).
- The S3 metadata-read boundary is now consistent with every other
  trust boundary's NaN / inf / unparseable handling (BL-159 cosine,
  BL-201 OpenAI rows, BL-204 SKILL.md YAML, BL-205 MultiDispatcher
  weights, BL-215 SKILL.md decode, BL-217 subprocess metadata,
  BL-221 BudgetTracker). The BL-225 evict-to-capacity per-key delete
  fan-out is now consistent with every other fan-out boundary's
  per-item containment (BL-222 MultiDispatcher, BL-223 MultiSink).
- An operator pointing `BoundedS3Store` at a bucket whose objects
  were partially written by a different process (with a different
  metadata schema, or a partially-corrupted prefix) no longer has
  the whole sweep / eviction crash on the first malformed entry.
- An operator running `TTLSweeper(store, max_keys=N)` against a
  transiently-throttled S3 region no longer loses every audit event
  for the keys that *did* get deleted before the throttle hit. The
  audit-vs-raise parity invariant is upheld at every fan-out leg of
  the new BL-225 path.

## Revisit triggers

The open items this audit deliberately did not touch:

- `BL-120` (live reference workload). Needs a funded provider key.
- `BL-132` / `BL-171` (prompt caching on the runtime adapter).
  Upstream-dependent on a verified PydanticAI provider-cache API
  plus a live model to validate.
- `BL-113` / `BL-138` (true OTel spans + GenAI semantic
  conventions). Upstream-dependent on the OTel logs SDK GA.
- `BL-114` (deeper PydanticAI resume). Upstream-dependent.
- `BL-135` open half (compaction / summarisation / tiering). The
  size-bound half is now closed across every in-tree adapter
  (BL-212 / BL-213 / BL-214 / BL-224 / BL-225); the long-horizon
  context-engineering half (summarisation, tiering hot-to-cold)
  remains.
- `BL-155` (true wall-clock preemption). Needs a thread/process
  execution boundary; the asyncio `await`-based watchdog is the
  documented preempt-at-yield-point shape.
- `BL-179` (`RetryPolicy` partial usage accounting). Upstream-
  dependent on PydanticAI exposing partial usage on exception.
- `S3Store._sweep_sync` per-key HEAD / DELETE containment. The
  parent's sweep has the same "one transient error kills the whole
  loop" shape that BL-227 fixes for `evict_to_capacity`, but the
  parent emits no audit (`sweep_expired` only returns a count), so
  there is no audit-vs-raise parity to violate. A defence-in-depth
  containment fix on the parent would match BL-227's shape and may
  land in a future audit, but it would change observable failure
  behaviour for callers that today expect `sweep_expired` to raise
  on the first failure -- deferred pending a clearer "should the
  parent sweep be best-effort too?" decision.
- `RoutingChainDispatcher` per-link exception fall-through (open
  from ADR 0019, unchanged). Needs a clear semantic for which
  exception classes constitute "fall through" vs "propagate" on the
  chain boundary.
