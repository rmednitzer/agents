"""Exception hierarchy for the workloads package."""

from __future__ import annotations

__all__ = [
    "ContractNotFound",
    "ManifestNotFound",
    "WorkloadError",
    "WorkloadNotFound",
    "WorkloadValidationError",
]


class WorkloadError(Exception):
    """Base for workloads-package errors."""


class WorkloadNotFound(WorkloadError):
    """The named workload package does not exist or is not importable."""

    def __init__(self, name: str, reason: str | None = None) -> None:
        suffix = f": {reason}" if reason else ""
        super().__init__(f"Workload '{name}' not found{suffix}")
        self.name = name
        self.reason = reason


class ManifestNotFound(WorkloadError):
    """The workload's manifest.yaml is missing."""

    def __init__(self, name: str, path: str) -> None:
        super().__init__(f"Manifest for workload '{name}' not found at {path}")
        self.name = name
        self.path = path


class ContractNotFound(WorkloadError):
    """The workload's contract.py is missing or does not export 'contract'."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"Contract for workload '{name}' not found: {reason}")
        self.name = name
        self.reason = reason


class WorkloadValidationError(WorkloadError):
    """The workload's manifest failed schema validation."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"Workload '{name}' manifest invalid: {reason}")
        self.name = name
        self.reason = reason
