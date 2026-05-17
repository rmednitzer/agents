# memory/

Memory backends, schemas, retrieval. Each backend exposes typed read/write per namespace with documented retention, isolation, and lineage guarantees.

Adapters: `InMemoryStore` (reference), `SQLiteStore` (stdlib, durable), `RedisStore`, `S3Store`, `DynamoDBStore` (lazy optional drivers behind the `redis`/`aws` extras; the package imports without them). Extension Protocols sit beside the core `MemoryStore`: `BatchMemoryStore`, `ScannableStore`, `ContentAddressableStore`, `CASMemoryStore`, `SweepableStore` (+ `TTLSweeper`). Decorators: `EncryptedStore` (AES-256-GCM, `crypto` extra) and `ACLStore` (role/prefix), with `wrap_encrypted` / `wrap_acl` factories that forward the wrapped backend's extension Protocols. Optional audit events via `MemoryAudit` (`sink` + `base_event_fields`). External adapters offload blocking I/O to threads so they do not stall an asyncio loop. See [ADR 0007](../docs/adr/0007-l2-implementation-wave.md) and [ADR 0010](../docs/adr/0010-l3-default-path-wiring-and-audit-wave.md).

Cross-namespace access is a contract violation. Tests must cover isolation explicitly.

Documented deviations and decorator scope:

- TTL precision: `InMemoryStore` / `SQLiteStore` / `S3Store` / `DynamoDBStore` keep float seconds (`DynamoDBStore` since `BL-157`), `RedisStore` uses milliseconds. The adapter-level lazy expiry, reads, scans, sweeps, and CAS all honour a sub-second TTL. DynamoDB's *native* server-side TTL sweep still reads only the integer part of `exp` and is best-effort/lagging by design (`LIMITATIONS.md` L13).
- S3 is eventually consistent: a read just after a write or delete may see the prior state.
- Decorator scope: the bare `EncryptedStore(...)` / `ACLStore(...)` constructors expose only the core `MemoryStore` surface (L1/L2 compat). Use `wrap_encrypted` / `wrap_acl` (`BL-156`) to layer a capability-rich backend: they compose a decorator forwarding exactly the extension Protocols the inner store satisfies, so `isinstance` stays truthful. `wrap_encrypted` deliberately does NOT forward `CASMemoryStore` even over a CAS backend: AES-GCM's per-write random nonce makes a ciphertext-equality compare-and-set unrepresentable, so encryption over CAS drops CAS by design rather than faking it (`LIMITATIONS.md` L12). Both decorators validate keys before any keyed operation, per the Protocol (the inner store also validates; for `EncryptedStore` the early check also prevents an AAD cross-key collision).
- Audit base fields: `MemoryAudit` rejects at construction both a partial base-event dict (missing a required key) and one carrying a reserved per-event key (`namespace`, `key`, ...), so a misconfiguration fails at load, not mid-run.

See [CLAUDE.md](../CLAUDE.md) for conventions.
