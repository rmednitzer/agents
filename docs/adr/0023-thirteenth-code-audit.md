# ADR 0023: Thirteenth code audit, additive hardening

- Status: Accepted
- Date: 2026-06-06
- Authors: rmednitzer
- Builds on: ADR 0001-0022

## Context

A thirteenth in-depth audit of `harness`, `memory`, `skills`,
`workloads`, the CLI, `evaluation`, and the offline gates, run against
the same green gates (ruff, ruff format, mypy strict, pytest at
`cov-fail-under=94`, schema-drift, REUSE 3.x, `pip-audit`, the dispatch
evaluation gate at P@1 = MRR = 1.0).

This audit re-walked the **fan-out per-member failure containment**
class (BL-222 `MultiDispatcher`, BL-223 `MultiSink`, BL-227
`BoundedS3Store.evict_to_capacity`, BL-228 `RoutingChainDispatcher`)
against the one sibling surface no prior audit had reached: the
**periodic TTL sweep** path. The class is: a loop over independent
members issues a per-member side effect (a dispatch, a sink emit, an
object DELETE); a single member's failure must not abort the rest of
the loop, or it breaks downstream delivery / telemetry / audit-vs-raise
parity. `BaseException` always still propagates (the BL-165 invariant).

BL-227 closed this class for `BoundedS3Store.evict_to_capacity`, whose
per-key `delete_object` loop now contains a transient error. But the
**sibling sweep path on the same adapter** (`S3Store._sweep_sync`) and
its DynamoDB twin (`DynamoDBStore._sweep_sync`) were not reached: both
issue a per-item network DELETE (`delete_object` / `delete_item`)
inside a Python loop with **no containment**. This was not a blind
spot, it was an explicitly tracked open question: the BL-229
`_head_metadata` docstring (ADR 0021), and the revisit-triggers
sections of ADR 0020, ADR 0021, and ADR 0022, all named "should the
parent sweep be best-effort for transient errors too?" as deferred.
This audit answers it.

The audit found one finding spanning both network adapters that have
the per-item-loop shape (the same reason BL-227 was S3-specific):

- **BL-233**: `S3Store._sweep_sync` and `DynamoDBStore._sweep_sync`
  iterate over scanned items and issue a per-item network DELETE with
  no exception containment. A single transient backend error (S3
  `SlowDown` / throttle, DynamoDB `ProvisionedThroughputExceeded`, a
  network blip) on one expired item propagates out of the loop and
  aborts the entire sweep pass: every later expired item in the same
  listing / scan is left un-swept for the cycle, and the count of the
  items already deleted in this pass is discarded (the function raises
  instead of returning). The `TTLSweeper` loop survives (BL-199 records
  the failure and retries next interval), but each retry re-LISTs and
  re-HEADs the whole keyspace from the start, and a steady low rate of
  transient errors can keep a large keyspace's tail permanently
  un-swept.

The recurring lesson holds and is sharpened: a per-member side-effect
loop over independent members must contain a per-member `Exception` so
one member's transient failure does not abort the rest. The sweep path
is the periodic-maintenance peer of the eviction path BL-227 already
hardened; this audit brings it to the same standard, and the
containment is consistent within each adapter (S3's sweep now matches
S3's evict).

## Decision

### 1. `S3Store._sweep_sync` per-object DELETE containment (BL-233)

The S3 sweep's per-object flow is: HEAD the object (`_head_metadata`)
to read its `expires-at` metadata, then, if expired, DELETE it. The
DELETE was bare:

```python
if is_expired(time.time(), exp):
    self._s3.delete_object(Bucket=self._bucket, Key=item["Key"])
    removed += 1
```

The fix contains a per-object DELETE failure, mirroring
`BoundedS3Store.evict_to_capacity._delete_all` (BL-227):

```python
if not is_expired(time.time(), exp):
    continue
try:
    self._s3.delete_object(Bucket=self._bucket, Key=item["Key"])
except Exception:
    continue
removed += 1
```

Two deliberate scope decisions:

- **The HEAD stays fail-loud.** `_head_metadata` (BL-229) already
  treats a not-found object as absent (skip) and propagates every
  other `ClientError` (AccessDenied, throttle, outage, NoSuchBucket).
  That is unchanged: an object the sweeper cannot *inspect* still
  surfaces as an error, so the sweep never silently skips a keyspace
  it has lost read access to. Only the idempotent DELETE *action*
  becomes best-effort, the same split BL-227 already established for
  the eviction path. The principled distinction: fail loud on the
  inspection so the keyspace is not silently masked; be best-effort on
  the idempotent action so one item's transient failure does not abort
  the batch.
- **`BaseException` still propagates.** The bare `except Exception`
  excludes `KeyboardInterrupt` / `SystemExit` / `asyncio.CancelledError`,
  the BL-165 / BL-223 / BL-227 / BL-228 terminal-signal invariant.

The sweep path emits no per-delete audit event (a TTL expiry removes a
logically-dead entry that `read` already reports absent, so unlike
`evict_to_capacity`'s capacity-driven deletes it is not individually
audited). The fix therefore has no audit-emit ordering to preserve: it
is purely "do not abort the pass". `removed` now counts only the
deletes that actually applied and is always returned, closing a minor
secondary gap (the pre-fix abort discarded the count of the items it
had already deleted in the failing pass).

### 2. `DynamoDBStore._sweep_sync` per-item DELETE containment (BL-233)

The DynamoDB twin. The scan reads `exp` inline (no separate HEAD), then
the loop issues a per-item `delete_item`:

```python
if is_expired(now, float(exp) if exp is not None else None):
    self._db.delete_item(TableName=self._table, Key={"pk": item["pk"]})
    removed += 1
```

The same containment is applied (`try/except Exception: continue`, count
only successes, `BaseException` propagates). The `Scan` stays fail-loud
for the same reason the S3 HEAD does.

A deliberate asymmetry is preserved and documented at the call site:
`DynamoDBStore`'s eviction path (`BoundedDynamoDBStore.evict_to_capacity`)
stays **all-or-nothing** via `_batch_write`-with-retry (it raises on
retry-budget exhaustion), because a bounded operation that must meet a
declared capacity cap differs from unbounded periodic maintenance.
S3's eviction is per-key best-effort (BL-227) and S3's sweep is now per-
object best-effort (this finding), so S3 is internally consistent;
DynamoDB's sweep is per-item best-effort while its eviction is batched,
which is the right shape for each operation rather than a uniformity
for its own sake.

### Why only these two surfaces

The other in-tree adapters' sweeps are bulk, with no per-item network
DELETE loop that could partially fail mid-pass:

- `InMemoryStore.sweep_expired`: in-process dict deletions (no I/O).
- `SQLiteStore.sweep_expired`: one `DELETE ... WHERE expires_at < :now`
  statement (atomic, no per-key loop).
- `BoundedRedisStore.sweep_expired`: a single `zrem(*stale)` over the
  auxiliary index after a bulk liveness check.

So the finding is exactly the two network adapters that issue a per-
item network DELETE inside a Python loop, the same scoping reason
BL-227 was S3-specific.

## Consequences

- The fix is additive to the L1 Protocols (ADR 0007). No public
  signature changed. The happy path (no DELETE error) is byte-identical:
  the same items are deleted and the same count returned. Only the
  error path changes, from "abort the whole sweep pass and raise" to
  "skip the failed item, continue, return the count of successes".
- A transient backend error on one expired item no longer strands every
  later expired item in the same listing / scan. Combined with the
  BL-199 `TTLSweeper` resilience contract (the loop survives a sweep
  that raises and retries next interval), the sweep is now resilient at
  two levels: one item's transient failure does not abort one pass, and
  one pass's failure does not kill the maintenance loop.
- S3's sweep and eviction paths are now consistent (both per-item best-
  effort), so a backend author copying either as a template sees the
  same containment shape.
- 8 new regression tests
  (`tests/memory/test_bl233_sweep_delete_containment.py`), 4 per
  adapter, using the BL-226 / BL-227 `moto` + flaky-client pattern:
  - a partial failure (one item's DELETE throttles) sweeps the rest and
    returns the count of successes without raising; the failed item
    stays alive on the backend;
  - every DELETE failing returns 0 without raising and leaves every item
    alive (retried next cycle, the BL-199 contract);
  - the happy path is unchanged by the per-item `try/except` (all
    expired items swept, count exact);
  - a `SystemExit` (a `BaseException`) on DELETE still propagates
    (the BL-165 / BL-223 invariant). Verified to fail against the
    pre-fix code (the four containment cases raise the injected
    `ClientError`; the happy-path and base-exception cases are
    fix-independent and pass either way).
- Coverage stays above the 94% gate.

## Revisit triggers

- **A *persistent* DELETE failure is now best-effort-silent on the
  sweep path.** A credential that can `ListBucket` + `HeadObject` (or
  `Scan`) but not `DeleteObject` (or `DeleteItem`) makes the sweep
  return 0 every cycle with no `TTLSweeper.failures_total` signal,
  because the per-item DELETE error is now contained rather than
  raised. This is a narrow IAM-misconfiguration edge, and it is exactly
  the trade-off `evict_to_capacity` already accepted under BL-227 (the
  inspection step still fails loud, so a credential that cannot read
  the keyspace at all still surfaces). Surfacing a *persistent*
  per-item failure through the sweeper's failure counters, without
  losing the best-effort behaviour on a *transient* one, would need a
  sweep return shape that carries both a success count and a failure
  count (the current `int` return carries only the count). That is a
  `TTLSweeper`-contract change, deliberately not taken here; it is a
  tracked follow-up if a third sweep-observability need appears
  (a consolidation candidate alongside the BL-227 eviction path).
- The standing open items unchanged by this audit: `BL-120` (live
  reference workload), `BL-132` / `BL-171` (prompt caching),
  `BL-113` / `BL-138` (true OTel spans), `BL-114` (deeper resume),
  `BL-135` open half (compaction / summarisation / tiering),
  `BL-155` (true wall-clock preemption), `BL-179` (`RetryPolicy`
  partial-usage accounting). The bare-float control-*parameter*
  surfaces (`drift_threshold`, dispatcher `threshold`) remain the
  deliberate non-finding of ADR 0022 (optional alerting /
  cost-optimisation knobs, not safety / resource ceilings).
