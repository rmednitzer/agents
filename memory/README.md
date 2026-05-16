# memory/

Memory backends, schemas, retrieval. Each backend exposes typed read/write per namespace with documented retention, isolation, and lineage guarantees.

Adapters: `InMemoryStore` (reference), `SQLiteStore` (stdlib, durable), `RedisStore`, `S3Store`, `DynamoDBStore` (lazy optional drivers behind the `redis`/`aws` extras; the package imports without them). Extension Protocols sit beside the core `MemoryStore`: `BatchMemoryStore`, `ScannableStore`, `ContentAddressableStore`, `CASMemoryStore`, `SweepableStore` (+ `TTLSweeper`). Decorators: `EncryptedStore` (AES-256-GCM, `crypto` extra) and `ACLStore` (role/prefix). Optional audit events via `MemoryAudit` (`sink` + `base_event_fields`). External adapters offload blocking I/O to threads so they do not stall an asyncio loop. See [ADR 0007](../docs/adr/0007-l2-implementation-wave.md).

Cross-namespace access is a contract violation. Tests must cover isolation explicitly.

See [CLAUDE.md](../CLAUDE.md) for conventions.
