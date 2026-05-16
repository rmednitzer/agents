# ADR 0004: Memory namespace contracts

- Status: Accepted
- Date: 2026-05-16
- Authors: rmednitzer
- Builds on: ADR 0002 (behavioral contracts)

## Context

Workloads need persistent state across runs (session data, retrieved
documents, learned facts, cached results, audit trails). The harness has
to expose a stable interface for this without locking in a specific
backend (in-memory for tests and dev, Redis or DynamoDB or SQLite for
production, S3 for cold storage).

Two cross-cutting requirements drive the design:

1. Workloads must not interfere with each other's memory. A multi-tenant
   harness instance running several workloads cannot allow workload A to
   read workload B's keys, even by accident.
2. The audit boundary needs the memory layer to be inspectable in the
   same structured-event style as the rest of the harness, so memory
   operations show up alongside contract events in audit packs (deferred
   to L2; the event surface is already in place via the EventSink
   Protocol).

The Bhardwaj 2026 paper does not address memory explicitly, but its
contract surface assumes pre/post conditions can be expressed over
observable state; if that state lives in shared memory, isolation
becomes a contract concern.

## Decision

### 1. Namespace-bound stores, not namespace-as-parameter

A MemoryStore is bound to a single Namespace at construction. The
read/write/delete/list_keys methods do not accept a namespace
parameter: the store already knows. A workload that needs two
namespaces (e.g. ephemeral session vs long-term knowledge) holds two
MemoryStore instances.

Cross-namespace access is impossible by construction. The harness does
not need to enforce ownership at every call; the type system does.

### 2. Bytes on the wire

The MemoryStore interface accepts and returns bytes. Workloads
serialize their data (JSON, MessagePack, Pickle, Protobuf, plain text).
The store stays format-agnostic, which makes the surface adapter-stable:
a Redis adapter and an S3 adapter and an in-memory adapter all expose
the same bytes-in / bytes-out contract.

This rules out a richer typed interface (e.g. write(key, BaseModel)) at
the cost of forcing workloads to serialize. The tradeoff is worth it:
workloads already need to choose a serialization format for their
domain models, and pushing that choice into the workload keeps the
store decoupled from Pydantic, Protobuf, or any specific format.

### 3. Key format rules enforced by validators

Keys are validated against:

- Non-empty.
- Max 256 characters.
- Must not contain '::' (the internal namespace separator).
- Must not contain path traversal patterns ('..', '/', '\\\\').
- Must not contain null bytes or whitespace.

Namespace names are validated against `^[a-z0-9][a-z0-9_-]{0,63}$`. The
patterns are conservative; they pass through cleanly as file path
components, URL path segments, Redis key prefixes, and S3 object keys.

Violations raise NamespaceViolation (a MemoryError subclass). The store
disambiguates "bad input" from "missing key": read() returns None for
nonexistent or expired keys but raises NamespaceViolation for
malformed keys. delete() is idempotent on nonexistent keys but raises
on malformed ones.

### 4. TTL precision in seconds, lazy expiry

Time-to-live is expressed as float seconds. Sub-second precision is
supported because asyncio time arithmetic is float-native; coarser
units would force quantization on the adapter side for no benefit.

Expiry is lazy: an expired entry is removed when next accessed (read
or list_keys). Active sweep is not required for correctness; adapters
that need it can run a background task. The InMemoryStore reference
does not sweep.

A namespace can carry retention_seconds as a default TTL. Explicit
ttl_seconds on write() overrides the default. None at both levels
means no expiry.

### 5. Concurrent writes: last-write-wins

The InMemoryStore serializes writes within a single store instance via
asyncio.Lock. The semantics are last-write-wins: the most recent
completed write determines the stored value. This matches what every
production KV backend gives (Redis SET, DynamoDB PutItem, S3 PutObject)
without optimistic concurrency control.

Workloads that need CAS or MVCC semantics must use an adapter that
exposes them (deferred). For L1, last-write-wins is sufficient.

## Consequences

Positive:

- Namespace isolation is structural. No runtime check is needed; the
  type system makes cross-namespace access impossible without explicit
  construction.
- The bytes-on-wire surface is small enough to fit on one page.
  Adapters are easy to write and easy to substitute.
- Key validation is centralized in memory.validators. Adapters do not
  reimplement it; they import and call.
- TTL semantics match what every real backend supports natively. No
  emulation layer needed.

Negative:

- Workloads pay the serialization cost. A workload that wants to store
  a Pydantic model must dump_json() on write and validate_json() on
  read. This is real overhead at the application layer, but it stays
  there.
- Concurrent writers in different processes against a shared backend
  need the adapter to provide atomicity. The Protocol does not promise
  cross-process safety; that is an adapter concern.

Neutral:

- The Protocol does not expose batch operations (mget, mset). Adapters
  can offer them as extensions, but the core surface stays minimal.

## Alternatives considered and rejected

- Namespace-as-parameter on every call. Rejected: requires per-call
  validation that the caller's namespace matches the workload's
  allowed namespaces. More surface, more failure modes, less
  type safety.
- Rich typed interface (write(key, BaseModel)). Rejected: couples the
  store to Pydantic, makes alternative serializations awkward,
  expands the adapter surface.
- Per-key namespace prefixes encoded into keys (e.g. "ns:foo/bar").
  Rejected: makes path traversal a real risk, makes keys longer for
  no observable benefit, requires every adapter to know the encoding.
- CAS / MVCC primitives in L1. Rejected: most production KV backends
  expose CAS via a separate method (Redis WATCH/MULTI, DynamoDB
  ConditionExpression); a single uniform interface adds complexity
  without immediate use cases.

## Deferred to L2

- Adapters: Redis (with pipelining and Lua scripts), SQLite (for
  durable single-host workloads), S3 (for blobs and audit packs),
  DynamoDB (for AWS-native deployments).
- Memory operation events (read / write / delete) feeding the EventSink
  for audit. Surface is ready; instrumentation is L2.
- Encryption at rest. Adapter-level concern.
- ACL / role-based access. The harness's contract layer covers
  authorization at the workload boundary; per-key ACLs are L2.
- Content addressing (write returns a content hash). Useful for
  immutable storage patterns; defer until first use case.
- CAS / MVCC primitives.
- Active TTL sweep (background task that removes expired entries).
- Multi-key batch operations (mget, mset).
- Iterator-style list_keys for very large keyspaces.

## Revisit triggers

This decision is revisited if:

- The bytes-on-wire constraint becomes painful for the majority of
  workloads (a typed convenience layer could be added on top without
  changing the Protocol).
- The 256-character key limit blocks a real use case (content
  addressing produces longer keys).
- TTL precision in seconds is too coarse (sub-millisecond use cases).
- Concurrent-write semantics need to differ between adapters in a way
  that breaks the Protocol's uniform contract.
