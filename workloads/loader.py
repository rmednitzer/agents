"""Workload bundle loader.

load_workload(name) imports `workloads.<name>` as a Python package,
finds the manifest.yaml in the package directory, parses it into a
WorkloadManifest, imports the package's contract module, and optionally
imports __main__.py for the entry point.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from harness.contract import Contract
from workloads.errors import (
    ContractNotFound,
    ManifestNotFound,
    WorkloadNotFound,
    WorkloadValidationError,
)
from workloads.manifest import WorkloadManifest

__all__ = [
    "LoadedWorkload",
    "load_workload",
]


@dataclass(frozen=True)
class LoadedWorkload:
    """A workload bundle resolved from its directory.

    Attributes:
        manifest: Parsed WorkloadManifest from manifest.yaml.
        contract: Contract object imported from contract.py.
        package_path: Filesystem path to the workload's package directory.
        main: Async entry point from __main__.py, if present. Calling
            convention is workload-defined.
    """

    manifest: WorkloadManifest
    contract: Contract[Any, Any]
    package_path: Path
    main: Callable[..., Awaitable[Any]] | None = None


def load_workload(name: str) -> LoadedWorkload:
    """Load a workload bundle by package name.

    Args:
        name: Workload package name. The loader imports
            `workloads.<name>` and reads its manifest.yaml.

    Returns:
        LoadedWorkload with manifest, contract, package_path, and
        optional main.

    Raises:
        WorkloadNotFound: The package does not exist or is not
            importable.
        ManifestNotFound: manifest.yaml is missing.
        ContractNotFound: contract.py is missing or does not export
            'contract'.
        WorkloadValidationError: manifest.yaml is not valid YAML, is not
            a mapping, or fails Pydantic validation.
    """
    try:
        pkg = importlib.import_module(f"workloads.{name}")
    except ImportError as exc:
        raise WorkloadNotFound(name, str(exc)) from exc

    pkg_file = getattr(pkg, "__file__", None)
    if pkg_file is None:
        raise WorkloadNotFound(name, "package has no __file__")
    package_path = Path(pkg_file).parent

    manifest_path = package_path / "manifest.yaml"
    if not manifest_path.is_file():
        raise ManifestNotFound(name, str(manifest_path))

    try:
        raw = yaml.safe_load(manifest_path.read_text())
    except yaml.YAMLError as exc:
        raise WorkloadValidationError(name, f"YAML parse: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkloadValidationError(name, f"manifest must be a mapping, got {type(raw).__name__}")
    try:
        manifest = WorkloadManifest.model_validate(raw)
    except ValidationError as exc:
        raise WorkloadValidationError(name, str(exc)) from exc

    try:
        contract_mod = importlib.import_module(f"workloads.{name}.contract")
    except ImportError as exc:
        raise ContractNotFound(name, f"cannot import contract module: {exc}") from exc

    contract = getattr(contract_mod, "contract", None)
    if contract is None:
        raise ContractNotFound(name, "contract module does not export 'contract'")
    if not isinstance(contract, Contract):
        raise ContractNotFound(
            name,
            f"contract export is not a Contract instance (got {type(contract).__name__})",
        )

    main: Callable[..., Awaitable[Any]] | None = None
    try:
        main_mod = importlib.import_module(f"workloads.{name}.__main__")
        main_candidate = getattr(main_mod, "main", None)
        if main_candidate is not None and callable(main_candidate):
            main = main_candidate
    except ImportError:
        pass

    return LoadedWorkload(
        manifest=manifest,
        contract=contract,
        package_path=package_path,
        main=main,
    )
