# workloads/

Individual agent workloads (missions). Each workload is a thin entry point that loads skills, runs inside the harness, and interacts with memory through declared interfaces.

Per-workload structure:
- `workloads/<name>/README.md` purpose and contract
- `workloads/<name>/__main__.py` entry point
- `workloads/<name>/manifest.yaml` skills loaded, harness target, memory namespace, exit conditions

See [CLAUDE.md](../CLAUDE.md) for conventions.
