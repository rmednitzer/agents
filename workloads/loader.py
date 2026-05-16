"""Workload bundle loader.

load_workload(name) imports `workloads.<name>` as a Python package,
finds the manifest.yaml in the package directory, parses it into a
WorkloadManifest, imports the package's contract module, and optionally
imports __main__.py for the entry point.

Two L2 validators (ADR 0007) run after the manifest parses:

- The manifest `name` must equal the package directory name. A silent
  mismatch is a deployment hazard (the operator runs `python -m
  workloads.<dir>` but the manifest, contract name, and memory namespace
  carry a different identity).
- If a SkillRegistry is supplied, every `skills:` entry must resolve in
  it. This is opt-in: skill resolution requires the caller to have built
  a registry, so the loader does not force one.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from skills.registry import SkillRegistry

__all__ = [
    "LoadedWorkload",
    "load_workload",
    "load_workload_from_path",
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


def load_workload(
    name: str,
    *,
    registry: SkillRegistry | None = None,
) -> LoadedWorkload:
    """Load a workload bundle by package name.

    Args:
        name: Workload package name. The loader imports
            `workloads.<name>` and reads its manifest.yaml.
        registry: Optional SkillRegistry. When supplied, every entry in
            the manifest's `skills:` list must resolve in it (BL-011);
            an unresolved skill raises WorkloadValidationError. When
            None, skill resolution is not checked.

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
            a mapping, fails Pydantic validation, declares a `name` that
            does not match the package directory (BL-010), or references
            a skill absent from `registry` (BL-011).
    """
    try:
        pkg = importlib.import_module(f"workloads.{name}")
    except ImportError as exc:
        raise WorkloadNotFound(name, str(exc)) from exc

    pkg_file = getattr(pkg, "__file__", None)
    if pkg_file is None:
        raise WorkloadNotFound(name, "package has no __file__")
    package_path = Path(pkg_file).parent

    def _import(submodule: str) -> ModuleType | None:
        try:
            return importlib.import_module(f"workloads.{name}.{submodule}")
        except ImportError:
            return None

    return _build_loaded_workload(name, package_path, _import, registry)


def load_workload_from_path(
    path: str | Path,
    *,
    registry: SkillRegistry | None = None,
) -> LoadedWorkload:
    """Load an out-of-tree workload from an arbitrary directory (BL-090).

    The directory need not be under the ``workloads`` package tree or on
    ``sys.path``: ``contract.py`` and the optional ``__main__.py`` are
    imported by file path under synthetic module names. The directory's
    own name is the workload identity for the BL-010 check, so the same
    name/skills validators apply as for in-tree workloads.

    Args:
        path: Filesystem path to the workload directory.
        registry: Optional SkillRegistry for the BL-011 skills check.

    Returns:
        LoadedWorkload with manifest, contract, package_path, optional main.

    Raises:
        WorkloadNotFound: The path is not an existing directory.
        ManifestNotFound: manifest.yaml is missing.
        ContractNotFound: contract.py is missing or invalid.
        WorkloadValidationError: manifest invalid, name does not match
            the directory (BL-010), or a skill is unresolved (BL-011).
    """
    package_path = Path(path).resolve()
    name = package_path.name
    if not package_path.is_dir():
        raise WorkloadNotFound(name, f"not a directory: {package_path}")

    def _import(submodule: str) -> ModuleType | None:
        file = package_path / f"{submodule}.py"
        if not file.is_file():
            return None
        mod_name = f"_oot_workload_{name}_{submodule}".replace("-", "_")
        spec = importlib.util.spec_from_file_location(mod_name, file)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _build_loaded_workload(name, package_path, _import, registry)


def _build_loaded_workload(
    name: str,
    package_path: Path,
    import_submodule: Callable[[str], ModuleType | None],
    registry: SkillRegistry | None,
) -> LoadedWorkload:
    """Shared manifest parse + L2 validators + contract/main resolution.

    ``import_submodule(stem)`` returns the workload's ``contract`` /
    ``__main__`` module (or None if absent); the in-tree and
    out-of-tree loaders differ only in how that import is performed.
    """
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

    # BL-010: manifest identity must match the package directory name.
    if manifest.name != package_path.name:
        raise WorkloadValidationError(
            name,
            f"manifest name {manifest.name!r} does not match package "
            f"directory name {package_path.name!r}",
        )

    # BL-011: every declared skill must resolve when a registry is given.
    if registry is not None:
        missing = [s for s in manifest.skills if s not in registry]
        if missing:
            raise WorkloadValidationError(
                name,
                f"skills not found in registry: {', '.join(sorted(missing))}",
            )

    try:
        contract_mod = import_submodule("contract")
    except ImportError as exc:
        raise ContractNotFound(name, f"cannot import contract module: {exc}") from exc
    if contract_mod is None:
        raise ContractNotFound(name, "contract.py is missing")

    contract = getattr(contract_mod, "contract", None)
    if contract is None:
        raise ContractNotFound(name, "contract module does not export 'contract'")
    if not isinstance(contract, Contract):
        raise ContractNotFound(
            name,
            f"contract export is not a Contract instance (got {type(contract).__name__})",
        )

    main: Callable[..., Awaitable[Any]] | None = None
    main_mod = import_submodule("__main__")
    if main_mod is not None:
        main_candidate = getattr(main_mod, "main", None)
        if main_candidate is not None and callable(main_candidate):
            main = main_candidate

    return LoadedWorkload(
        manifest=manifest,
        contract=contract,
        package_path=package_path,
        main=main,
    )
