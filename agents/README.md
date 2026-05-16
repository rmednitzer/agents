# agents/

Operator CLI for the substrate. `python -m agents <command>` (or the
`agents` console script) over the existing public APIs; it adds no
behaviour the libraries do not already expose.

- `workloads list` — every loadable workload bundle (BL-020).
- `skills list` — every skill under `skills/`, grouped by lane (BL-022).
- `run <workload> <query>` — load a workload, optionally dispatch a
  skill (deterministic KeywordDispatcher, no API key), run it under
  contract, print the structured result (BL-021).

See [CLAUDE.md](../CLAUDE.md) and ADR 0007.
