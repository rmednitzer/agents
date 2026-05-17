# memory/

Memory backends, schemas, retrieval. Each backend exposes typed read/write per namespace with documented retention, isolation, and lineage guarantees.

Adapters: `InMemoryStore` (reference), `SQLiteStore` (stdlib, durable), `RedisStore`, `S3Store`, `DynamoDBStore` (lazy optional drivers behind the `redis`/`aws` extras; the package imports without them). Extension Protocols sit beside the core `MemoryStore`: `BatchMemoryStore`, `ScannableStore`, `ContentAddressableStore`, `CASMemoryStore`, `SweepableStore` (+ `TTLSweeper`). Decorators: `EncryptedStore` (AES-256-GCM, `crypto` extra) and `ACLStore` (role/prefix). Optional audit events via `MemoryAudit` (`sink` + `base_event_fields`). External adapters offload blocking I/O to threads so they do not stall an asyncio loop. See [ADR 0007](../docs/adr/0007-l2-implementation-wave.md).

Cross-namespace access is a contract violation. Tests must cover isolation explicitly.

Documented deviations and decorator scope:

- TTL precision: `InMemoryStore` / `SQLiteStore` / `S3Store` keep float seconds, `RedisStore` uses milliseconds, `DynamoDBStore` truncates to integer seconds. A sub-second TTL on DynamoDB rounds down; ADR 0004's "sub-second precision" does not hold for that adapter (`LIMITATIONS.md` L13, `BL-157`).
- S3 is eventually consistent: a read just after a write or delete may see the prior state.
- `EncryptedStore` and `ACLStore` implement the core `MemoryStore` surface only. They do not forward `BatchMemoryStore` / `ScannableStore` / `ContentAddressableStore` / `CASMemoryStore` / `SweepableStore`, so layering them over a capable backend hides those capabilities (`LIMITATIONS.md` L12, `BL-156`). Both validate keys before any keyed operation, per the Protocol (the inner store also validates; for `EncryptedStore` the early check also prevents an AAD cross-key collision).

See [CLAUDE.md](../CLAUDE.md) for conventions.
