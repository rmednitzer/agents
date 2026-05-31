# ADR 0021: Eleventh code audit, additive hardening

- Status: Accepted
- Date: 2026-05-31
- Authors: rmednitzer
- Builds on: ADR 0001-0020

## Context

An eleventh in-depth audit of `harness`, `memory`, `skills`,
`workloads`, the CLI, `evaluation`, and the offline gates, run against
the same green gates (ruff, ruff format, mypy strict, pytest at
`cov-fail-under=94`, schema-drift, REUSE 3.x, `pip-audit`, the dispatch
evaluation gate at P@1 = MRR = 1.0). The prior audits (ADR 0009 / 0010
/ 0011 / 0013 / 0015 / 0017 / 0018 / 0019 / 0020) closed a wide
surface. This audit had two explicit starting points: the two open
revisit triggers ADR 0020 deliberately deferred (the
`RoutingChainDispatcher` per-link exception fall-through and the
`S3Store._sweep_sync` per-key HEAD / DELETE containment), plus a fresh
re-walk of the recurring fault classes (NaN at trust boundaries,
unparseable input, fan-out containment, audit-vs-raise parity,
LIST-then-HEAD concurrency) against any surface a prior audit fixed
only pointwise.

The audit found two new findings and one deliberate non-finding:

- **BL-228**: `RoutingChainDispatcher.dispatch` (`skills/dispatchers/chain.py`)
  iterated its cheap-first chain with no per-link exception
  containment, so a single raising link (a network `LLMDispatcher`
  raising `DispatchError` or timing out, an embedding provider blip)
  crashed the whole chain and discarded the best-effort fallback the
  chain documents, including the matches already gathered from the
  cheaper links that ran first. This is the BL-222 (`MultiDispatcher`)
  / BL-223 (`MultiSink`) / BL-227 (`BoundedS3Store.evict_to_capacity`)
  per-item fan-out containment class, unapplied on the one sequential
  fan-out the prior audits had not reached. `default_dispatcher`
  (BL-103, the recommended default routing composition) wraps a
  `RoutingChainDispatcher`, so the failure mode was on the default
  path.

- **BL-229**: `S3Store._sweep_sync` and `BoundedS3Store._collect_live_sync`
  (`memory/s3.py`) LIST the namespace prefix and then `head_object(...)`
  each listed key directly. If an object is deleted between the LIST
  and the HEAD (S3's documented concurrent-access / eventual-consistency
  window: another writer, a concurrent `read` lazy-expiry delete, or a
  concurrent `sweep_expired` run), HeadObject returns HTTP 404
  (`NoSuchKey`) and the raw call crashed the whole sweep / eviction
  scan. The `_get_live` read path already treats this not-found case
  as "absent", but the two metadata-scan loops bypassed `_get_live`
  and lacked the guard. The `_collect_live_sync` half is new (BL-227
  contained only `evict_to_capacity`'s per-key DELETE loop, not the
  collect-phase HEAD); the `_sweep_sync` half is the narrow,
  unambiguous resolution of the ADR 0020 revisit trigger.

- **Deliberate non-finding (DynamoDB `float(exp)`)**: `memory/dynamodb.py`
  parses its `exp` attribute via bare `float(exp) if exp is not None
  else None` at four sites (`_live_item`, `_list_sync`, `_scan_sync`,
  and the `compare_and_set` match branch). On the surface this is the
  exact bare-`float` pattern BL-226 replaced with `_safe_float` in
  `memory/s3.py`. It was checked against the DynamoDB data-type
  documentation and deliberately left unchanged (see Decision 3).

The recurring lesson holds: every fan-out path and every read-back of
a value from a backend store is a candidate re-instance of an
invariant a prior audit generalised on a neighbouring surface. The
audit verifies the class generalises to every new boundary, and is
equally disciplined about where a boundary's own guarantees make the
class inapplicable.

## Decision

### 1. RoutingChainDispatcher per-link failure containment (BL-228)

A BL-222 / BL-223 / BL-227 fan-out containment class extension onto the
sequential cheap-first chain.

The loop was:

```python
for dispatcher in self._chain:
    matches = await dispatcher.dispatch(query, context=context, limit=limit)
    if matches:
        last_matches = matches
        if matches[0].confidence >= self._threshold:
            return matches
return last_matches
```

A raising link propagated straight out of `dispatch`. The fix wraps the
per-link call in `try / except Exception` and falls through on failure:

```python
for dispatcher in self._chain:
    try:
        matches = await dispatcher.dispatch(query, context=context, limit=limit)
    except Exception:
        continue
    if matches:
        last_matches = matches
        if matches[0].confidence >= self._threshold:
            return matches
return last_matches
```

The clear fall-through-vs-propagate semantic the ADR 0019 / ADR 0020
revisit trigger asked for:

- A link that raises `Exception` (an operational error: `DispatchError`
  from an LLM-backed link on a malformed response, a network timeout,
  an embedding provider error) is treated as "this link produced no
  usable match". The chain falls through to the next link, exactly as
  it already does for a link that returns an empty list. This matches
  the dispatcher's documented best-effort-fallback contract: a raising
  link is the failure analogue of a link that returned nothing.
- `BaseException` (`KeyboardInterrupt`, `SystemExit`,
  `asyncio.CancelledError`) is NOT contained (the bare `except
  Exception` excludes it), so terminal signals reach the caller. This
  is the BL-165 / BL-222 / BL-223 invariant.

If every link fails the chain returns `last_matches`, which is the
empty list when no earlier link returned anything: parity with the
existing all-empty case and with `MultiDispatcher`'s BL-222 all-fail
return. An operator watching routing health still sees the degradation:
`default_dispatcher` wraps the chain in an `InstrumentedDispatcher`
(BL-103 / BL-207), whose `matched` / `fallback_rate` telemetry reflects
a chain that returned no match.

### 2. S3 metadata-scan HEAD not-found containment (BL-229)

A BL-170 (S3 listing robustness) and `_get_live`-not-found-idiom class
extension onto the two metadata-scanning HEAD loops, plus the narrow
resolution of the ADR 0020 `_sweep_sync` revisit trigger.

`S3Store._get_live` already treats a not-found object as "absent, not
an error" and propagates every other `ClientError` so an outage is
never misreported as an absent key (the existing stance documented at
that call site). The two scan loops that bypass `_get_live` and HEAD
each listed key did not share the guard:

```python
for item in resp.get("Contents", []):
    head = self._s3.head_object(Bucket=self._bucket, Key=item["Key"])
    exp = _safe_float(head.get("Metadata", {}).get(_EXPIRES_META))
    ...
```

The S3 HeadObject API documentation confirms a missing key returns HTTP
status `404 NoSuchKey`. Under S3's documented concurrent-access model
the LIST-then-HEAD window is expected to open (a concurrent writer's
delete, a concurrent `read` lazy-expiry delete, or a second sweep), so
a single concurrently-deleted object crashed the whole `sweep_expired`
/ `evict_to_capacity` scan, leaving every later expired or
over-capacity object unprocessed.

The fix is one helper on `S3Store`, mirroring the `_get_live`
not-found idiom exactly:

```python
def _head_metadata(self, s3_key: str) -> dict[str, str] | None:
    try:
        head = self._s3.head_object(Bucket=self._bucket, Key=s3_key)
    except self._s3.exceptions.NoSuchKey:
        return None
    except self._s3.exceptions.ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in ("NoSuchKey", "404", "NotFound"):
            return None
        raise
    metadata: dict[str, str] = head.get("Metadata", {})
    return metadata
```

`_sweep_sync` and `_collect_live_sync` call it and `continue` on
`None`. Two deliberate scope decisions:

- **Only not-found is contained.** Any other `ClientError` (throttle /
  `SlowDown`, `AccessDenied`, a transient outage, `NoSuchBucket`) still
  propagates, matching `_get_live`'s explicit "do not misreport an
  outage as an absent key" stance. This is the narrow consistency fix,
  not a blanket best-effort sweep, so it deliberately leaves the ADR
  0020 "should the parent sweep be best-effort for transient errors
  too?" design question open rather than silently answering it by
  swallowing real backend errors.

- **Only the HEAD needs the guard.** S3 DeleteObject is idempotent (a
  delete of an already-absent key returns success, not an error), so
  the DELETE in `_sweep_sync` cannot raise on a concurrently-deleted
  object and needs no equivalent guard. The DELETE fan-out in
  `evict_to_capacity` already has its own per-key containment from
  BL-227.

A concurrently-deleted object is treated as "already gone": `sweep`
does not count it (it was not swept by this call), and `evict` excludes
it from the live set (it cannot count toward the capacity cap). Both
are the correct accounting.

### 3. DynamoDB `float(exp)` left unchanged (deliberate non-finding)

The four `float(exp) if exp is not None else None` sites in
`memory/dynamodb.py` are the same bare-`float` shape BL-226 replaced in
`memory/s3.py`, but the DynamoDB boundary's own guarantees make the
BL-226 class inapplicable, so they are deliberately not changed:

- The DynamoDB Number (`N`) data type has a documented finite valid
  range (positive `1E-130` to `9.9999999999999999999999999999999999999E+125`,
  and the negative mirror), and "exceeding this results in an
  exception". DynamoDB server-side-validates every `N` attribute on
  write, so NaN, +inf, and -inf cannot be stored in an `N` attribute
  through its API. The S3 case was genuinely exploitable precisely
  because S3 user-metadata is free-form strings with no server-side
  numeric validation; that asymmetry is the whole reason BL-226
  applied to S3.
- The read path uses `item.get("exp", {}).get("N")`: an attribute
  written under a different schema as a String (`{"S": ...}`) yields
  `None` from `.get("N")` and is treated as no-TTL, so a wrong-type
  attribute is already handled safely without parsing.
- The largest valid DynamoDB number (`~1E+125`) is far inside the
  Python `float` range (`~1.8E+308`), so `float()` cannot overflow to
  `inf` on a server-validated value either.

Adding a `_safe_float` here would be defence-in-depth against a value
that the backend's own type system already forbids, at the cost of
implying (incorrectly) that the DynamoDB boundary is as untrusted as
the S3 metadata boundary. Recording the reasoning is the right
treatment, the same discipline ADR 0020 applied when it explained why
the parent `_sweep_sync` containment was deferred rather than rushed.

## Consequences

- The fixes are additive to the L1 Protocols (ADR 0007). No public
  signature changed; no caller behaviour changed on the happy path.
  The exception types still propagating on both boundaries
  (`BaseException` on the chain, every non-not-found `ClientError` on
  the S3 scans) are existing types, not new ones.
- `RoutingChainDispatcher` now degrades gracefully on the default path:
  an `LLMDispatcher`-tier failure in a `default_dispatcher` chain falls
  back to the keyword / embedding tier instead of surfacing as a
  whole-dispatch crash, while a high-confidence cheap match still
  short-circuits before any later (possibly failing) link is reached.
- `BoundedS3Store` and `S3Store` no longer crash a `sweep_expired` /
  `evict_to_capacity` cycle when an object is deleted in the
  LIST-then-HEAD window, which closes the collect-phase gap BL-227 did
  not cover and resolves the parent-sweep revisit trigger for the
  not-found case.
- 16 new regression tests:
  - `tests/skills/test_bl228_chain_member_failure.py` (7): failing
    middle link falls through to a later success; failing last link
    preserves the earlier best-effort match; failing first link does
    not abort; all links fail returns empty; a high-confidence cheap
    winner short-circuits before the failing link; `BaseException`
    (parametrized over `KeyboardInterrupt` / `SystemExit` /
    `CancelledError`) propagates; happy path unchanged.
  - `tests/memory/test_bl229_s3_head_toctou.py` (9): `_head_metadata`
    returns metadata for a present object, `None` on a 404
    `ClientError`, `None` on a typed `NoSuchKey`, and propagates a
    non-404 `ClientError`; `sweep_expired` skips a concurrently-deleted
    object and still sweeps the valid expired one, propagates a non-404
    HEAD error, and is unchanged on the happy path; `evict_to_capacity`
    skips a concurrently-deleted object and proceeds over the rest, and
    propagates a non-404 HEAD error.
- Coverage stays above the 94% gate.

## Revisit triggers

The open items this audit deliberately did not touch:

- `BL-120` (live reference workload). Needs a funded provider key.
- `BL-132` / `BL-171` (prompt caching on the runtime adapter).
  Upstream-dependent on a verified PydanticAI provider-cache API plus a
  live model to validate.
- `BL-113` / `BL-138` (true OTel spans + GenAI semantic conventions).
  Upstream-dependent on the OTel logs SDK GA.
- `BL-114` (deeper PydanticAI resume). Upstream-dependent.
- `BL-135` open half (compaction / summarisation / tiering). The
  size-bound half is closed across every in-tree adapter.
- `BL-155` (true wall-clock preemption). Needs a thread / process
  execution boundary.
- `BL-179` (`RetryPolicy` partial usage accounting). Upstream-dependent.
- `S3Store._sweep_sync` per-key containment for **transient** (non-not-found)
  errors. BL-229 fixed the not-found-consistency half; whether the
  parent sweep should also be best-effort for a transient throttle on
  one key (continue the rest of the keyspace instead of raising)
  remains the open design question ADR 0020 framed, now narrowed to
  exclude the not-found case. It still trades against the documented
  "an outage propagates" contract that callers may depend on, so it
  stays deferred pending a clear best-effort-sweep decision.
