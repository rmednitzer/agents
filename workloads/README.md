# workloads/

Individual agent workloads (missions). Each workload is a thin entry point that loads skills, runs inside the harness, and interacts with memory through declared interfaces.

Per-workload structure:
- `workloads/<name>/README.md` purpose and contract
- `workloads/<name>/__main__.py` entry point
- `workloads/<name>/manifest.yaml` skills loaded, harness target, memory namespace, exit conditions
- `workloads/<name>/contract.py` exports `contract: Contract[InputT, OutputT]`

`load_workload(name, *, registry=...)` resolves in-tree bundles and
validates that the manifest `name` matches the directory (BL-010) and
that every `skills:` entry resolves in the registry, including the
`name@version` form (BL-011). `load_workload_from_path(path)` loads
out-of-tree bundles from an arbitrary directory with the same
validators (BL-090). A real import failure in the package /
`contract.py` / `__main__.py` propagates as the original exception
rather than being masked as "not found". See
[ADR 0005](../docs/adr/0005-workload-bundles.md).

See [CLAUDE.md](../CLAUDE.md) for conventions.
