# ADR 0014: VersionedMemoryStore on durable adapters + multi-key transactions (BL-180)

- Status: Accepted
- Date: 2026-05-23
- Authors: rmednitzer
- Builds on: ADR 0001-0013

## Context

`BL-124` (ADR 0011) shipped the `VersionedMemoryStore` extension Protocol
(MVCC content-hash version tokens: `read_versioned`, `write_versioned`,
`delete_versioned`) with reference implementations on `InMemoryStore`
and `SQLiteStore`. The same ADR explicitly scoped two pieces forward as
`BL-180`: bringing the Protocol to the durable network adapters
(`RedisStore`, `DynamoDBStore`), and a separate multi-key-transaction
surface on every backend whose native primitives support it (SQLite's
`BEGIN IMMEDIATE`, Redis `MULTI`/`EXEC`, DynamoDB `TransactWriteItems`).

`S3Store` stays excluded for the same reason it does not implement CAS:
no atomic compare-and-set on object content; emulating one with
read-then-conditional-put would not be atomic against a concurrent
writer (ADR 0004 "don't fake it"). The `If-Match` / `If-None-Match`
ETag-based conditional-PUT surface S3 has added recently is over the
object ETag, not over our content-hash token, so introducing a parallel
version mechanism for S3 would either need a custom version attribute
in object metadata (with the same eventually-consistent visibility caveat
as the rest of `S3Store`) or a token-and-ETag round trip; both fail the
"contract one isinstance assertion can rely on" bar.

The user-visible effect: a workload now reaches every L2/L3 capability
on the durable backends through one `isinstance` per Protocol (BL-156
truthful-isinstance), the same surface the in-tree reference adapters
expose.

## Decision

### 1. `VersionedMemoryStore` on `RedisStore` (BL-180.a)

`RedisStore.read_versioned` returns `(value, sha256(value))` for a live
key; `write_versioned` and `delete_versioned` use the canonical
`WATCH`/`MULTI`/`EXEC` loop already used by `compare_and_set` /
`compare_and_delete` (BL-072), with the precondition switched from
bytes-equality to a content-hash comparison: a writer that holds a
token verifies the live value still hashes to that token between
`WATCH` and `MULTI`, then commits inside the transaction so a
concurrent write triggers `WatchError` and retries. The bounded
`_CAS_MAX_RETRIES = 50` give-up applies (a hot key cannot wedge the
caller); persistent contention returns `None` (write) / `False`
(delete), the BL-072 best-effort convention.

The token semantics are path-independent: any write that changes the
value changes the token, identical bytes yield an identical token
(content-version, the documented ABA-no-conflict; tested by the shared
`test_identical_content_is_no_conflict_aba` boundary case run across
every backend).

### 2. `VersionedMemoryStore` on `DynamoDBStore` (BL-180.b)

DynamoDB cannot evaluate SHA-256 server-side, so a one-round-trip
conditional PUT needs the token stored as an attribute. `_item` now
stamps `ver = sha256(value)` on every write path (`write`, `mset`,
`compare_and_set`); the attribute stays consistent with `v` by
construction. `read_versioned` still hashes the live `v` (not `ver`)
so the read path is path-independent even if a future code path forgets
to refresh `ver`. `write_versioned` and `delete_versioned` use one
conditional PUT/DELETE with `ConditionExpression = "ver = :e AND
(attribute_not_exists(exp) OR exp >= :now)"`, mirroring the
`compare_and_set` match-branch with `ver` replacing the bytes-equality
predicate. The `exp >= :now` live boundary matches `_live_item`'s
treatment of an expired row as absent (the read-vs-CAS boundary class
fixed for the other paths in BL-157/BL-177/BL-188), so an expired row
is `write_versioned`-creatable and `delete_versioned`-refused.

Migration contract: a row written before BL-180 has no `ver` attribute,
so `write_versioned(expected_version=correct_hash)` fails its
conditional (no silent success). The fix is a single plain `write()`,
which restamps `ver`. The behaviour is documented in
`memory/README.md` and `LIMITATIONS.md` L17, and exercised by the new
`test_write_versioned_against_legacy_row_without_ver_attribute`
regression. AES-GCM-style authenticated legacy fallback (BL-181) is not
applicable here: the bytes are not encrypted, so the token can be
recomputed from `v` at any time; the cost of forcing one upgrading
write is bounded and acceptable.

### 3. `TransactionalMemoryStore` Protocol + `TxnWrite` / `TxnDelete` (BL-180.c)

A new extension Protocol beside `VersionedMemoryStore`:

```python
@runtime_checkable
class TransactionalMemoryStore(MemoryStore, Protocol):
    async def transact(
        self,
        *,
        writes: Mapping[str, TxnWrite] | None = None,
        deletes: Mapping[str, TxnDelete] | None = None,
    ) -> dict[str, str] | None: ...
```

`TxnWrite(value, expected_version=None, ttl_seconds=None)` and
`TxnDelete(expected_version)` are frozen dataclasses. Every operation in
a transaction carries an `expected_version` referencing the same
content-hash token used by `VersionedMemoryStore`; the transaction
commits iff every precondition holds at commit time, otherwise it is a
no-op and `transact` returns `None`. A key appearing in both `writes`
and `deletes` is rejected at the contract boundary as a caller bug
(`ValueError`); an empty transaction is a legal no-op returning `{}`.

Backend mappings:

- `InMemoryStore`: serialized by the store's `asyncio.Lock`; the
  precondition pass and the apply pass run under one lock acquisition,
  so atomicity is trivial.
- `SQLiteStore`: one `BEGIN IMMEDIATE` transaction with per-key
  precondition checks and per-key `INSERT OR REPLACE` / `DELETE`
  inside; `ROLLBACK` on a precondition miss, `COMMIT` on success.
- `RedisStore`: `WATCH(all keys)` / sequential `GET`s and hash checks
  before `MULTI` / queued commands / `EXEC`, with `WatchError`-driven
  bounded retry (`_CAS_MAX_RETRIES`). Sequential `GET` (not `MGET`)
  inside the watched window is deliberate: `GET` runs immediately so
  the value is inspectable before `MULTI` starts queueing.
- `DynamoDBStore`: one `transact_write_items` call assembled from
  per-item `Put` / `Delete` operations, each with the same
  `ConditionExpression` `VersionedMemoryStore` uses
  (`ver = :e AND (attribute_not_exists(exp) OR exp >= :now)`, or
  `attribute_not_exists(pk) OR (attribute_exists(exp) AND exp < :now)`
  for `expected_version=None`).
  `TransactionCanceledException` whose `CancellationReasons` are all
  `ConditionalCheckFailed` (or the inert `None` marker for non-failing
  items) is the no-op signal; any other cancellation code (capacity,
  throttle, `ItemCollectionSizeLimitExceeded`) propagates so the caller
  does not emit success audit for dropped writes (the BL-033 retry
  posture from `_batch_write`).

The DynamoDB transaction is capped at 100 items per call (the AWS hard
limit on `TransactWriteItems`). The cap is checked at the contract
boundary with a clear `ValueError` naming the limit, not deferred to an
opaque `ClientError`.

`S3Store` and any future "no native multi-key atomicity" backend do not
implement this Protocol. Emulating it with per-key CAS would not be
atomic in the face of concurrent writers, the same "don't fake it"
boundary that excludes S3 from CAS / Versioned.

### 4. ACL forwarding (BL-156 invariant)

`wrap_acl` gains an `_ACLTransactionalMixin` so a `TransactionalMemoryStore`
inner store also exposes `transact` through the decorator, with the
guard run per touched key before the inner call. Mirrors the existing
`_ACLVersionedMixin` shape; `isinstance(wrapped, TransactionalMemoryStore)`
stays truthful for any ACL-wrapped transactional backend. A guard denial
raises `AccessDenied` before the inner store sees the call, so a
partial-permission principal cannot smuggle one un-authorised op into
an otherwise legal transaction.

`wrap_encrypted` deliberately does **not** forward `TransactionalMemoryStore`,
for exactly the reason it does not forward `CASMemoryStore` /
`VersionedMemoryStore`: AES-GCM's per-write random nonce makes the
ciphertext-equality (`v = :e`) / ciphertext-hash (`ver = :e`)
conditions unrepresentable. Encryption over a transactional backend
drops the transactional surface by design, the documented L12 / BL-124
deviation extended to BL-180.

## Consequences

- Every L2/L3 memory capability the framework documents
  (`MemoryStore`, `BatchMemoryStore`, `ScannableStore`,
  `ContentAddressableStore`, `CASMemoryStore`, `SweepableStore`,
  `VersionedMemoryStore`, `TransactionalMemoryStore`,
  `SemanticMemoryStore`) is now exposed by at least one in-tree
  reference adapter; `MemoryStore`, `BatchMemoryStore`,
  `ScannableStore`, `ContentAddressableStore`, `CASMemoryStore`,
  `VersionedMemoryStore`, and `TransactionalMemoryStore` are exposed
  by every applicable adapter. The "one in-tree reference for every
  Protocol" baseline from ADR 0011 is closed for the durable
  adapters too.
- `BL-124` is fully resolved (the InMemory/SQLite reference plus the
  durable adapter coverage); `BL-180` resolves with both parts
  (Versioned-on-durable + multi-key transactions) in one PR.
- A workload that wants atomic multi-key updates can now write portable
  code against `TransactionalMemoryStore` without choosing a backend at
  compile time; the lazy-import-with-extra pattern (ADR 0007) means a
  workload running against the in-tree default still type-checks
  cleanly.
- All changes are additive and preserve L1 import paths and signatures
  (ADR 0007). The default control flow and exceptions are unchanged
  for every existing call. The DynamoDB `ver` attribute is new but is
  ignored by every pre-BL-180 reader and round-trips through the
  existing `_live_item` path; the only observable behaviour change is
  that a legacy row's `write_versioned(expected_version=...)` returns
  `None` (correctness: no silent acceptance against a row whose `ver`
  attribute does not exist).
- Tests: 45 new test cases across `tests/memory/test_bl124_versioned.py`
  (now parametrised over all four backends) and the new
  `tests/memory/test_bl180_transactional.py`, plus the
  `test_write_versioned_against_legacy_row_without_ver_attribute`
  regression and `test_wrap_acl_forwards_transactional_protocol`
  ACL-forwarding test. The full memory suite is at 257 passes; the
  whole repo suite at 795 (with mypy strict, ruff, format, schema, and
  the evaluation gate all green).

## Revisit triggers

- If a future S3 client surface offers durable, content-hash-keyed
  atomic conditional writes that compose with a multi-object atomic
  commit (currently S3 has neither), revisit the S3 exclusion.
- If a workload needs token-stable encryption over a `Versioned` /
  `Transactional` backend, the path is a deterministic-nonce
  encryption mode (XChaCha20-Poly1305 with a deterministic nonce
  derived from the key, the per-key-AAD pattern, ...); the current
  AES-GCM-with-random-nonce contract precludes it by design. This is
  not currently planned.
- The `_CAS_MAX_RETRIES = 50` give-up budget is per-call best-effort;
  a workload with a hot key under sustained contention should
  application-layer back off rather than rely on the framework's
  internal retry. If the per-call give-up needs to be parameterisable,
  it is an additive keyword on the relevant methods, no contract
  change required.
- DynamoDB `TransactWriteItems` is billed at double the cost of a
  `BatchWriteItem` (DynamoDB pricing). A workload that does not need
  multi-key atomicity should use `mset` / `mdelete` (which already
  pipeline / retry per BL-033) rather than `transact`; this is a
  documentation note, not a framework concern.
