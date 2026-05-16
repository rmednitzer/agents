"""agents workloads: bundle convention, manifest schema, loader.

A workload is a Python package under `workloads/` with:

- __init__.py: makes the directory importable.
- manifest.yaml: declares identity, runtime, memory binding, MCP
  servers, skills, dispatcher, budget, exit conditions.
- contract.py: exports `contract: Contract[InputT, OutputT]`.
- __main__.py: optional, exports `main: async callable` for CLI use.
- README.md: optional.

See docs/adr/0005-workload-bundles.md.

The `_example` workload demonstrates the convention. It validates
markdown content against contract style conventions (no em-dashes,
no double dashes, H1 required at start).
"""

from workloads.errors import (
    ContractNotFound,
    ManifestNotFound,
    WorkloadError,
    WorkloadNotFound,
    WorkloadValidationError,
)
from workloads.loader import LoadedWorkload, load_workload, load_workload_from_path
from workloads.manifest import RuntimeSpec, WorkloadManifest

__all__ = [
    "ContractNotFound",
    "LoadedWorkload",
    "ManifestNotFound",
    "RuntimeSpec",
    "WorkloadError",
    "WorkloadManifest",
    "WorkloadNotFound",
    "WorkloadValidationError",
    "load_workload",
    "load_workload_from_path",
]
