# agents/

Operator CLI for the substrate. `python -m agents <command>` (or the
`agents` console script) over the existing public APIs; it adds no
behaviour the libraries do not already expose.

- `workloads list`: every loadable workload bundle (BL-020).
- `skills list`: every skill under `skills/`, grouped by lane (BL-022).
- `skills install <name> --from <src> [--dest <dir>]`: fetch and
  validate a skill from `local:<root>` or
  `github:<owner/repo>[@ref][:prefix]`; contract execution stays gated
  (`allow_contract=False`) (BL-125).
- `run <workload> <query> [--json]`: load a workload, optionally
  dispatch a skill (model-free; honours a `keyword`/`embedding`
  manifest dispatcher, else falls back to keyword), run it under
  contract, print the structured result (`--json` for compact output)
  (BL-021, BL-161). A missing-dependency import error is reported
  cleanly; a genuine bug in the workload body still surfaces.

See [CLAUDE.md](../CLAUDE.md), ADR 0007, and
[ADR 0010](../docs/adr/0010-l3-default-path-wiring-and-audit-wave.md).
