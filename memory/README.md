# memory/

Memory backends, schemas, retrieval. Each backend exposes typed read/write per namespace with documented retention, isolation, and lineage guarantees.

Cross-namespace access is a contract violation. Tests must cover isolation explicitly.

See [CLAUDE.md](../CLAUDE.md) for conventions.
