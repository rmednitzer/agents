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
import importlib.metadata
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
    "load_workload_from_entry_point",
    "load_workload_from_path",
]

# Entry-point group an installed package declares a workload under, e.g.
#   [project.entry-points."agents.workloads"]
#   my-workload = "my_pkg.workload"
_ENTRY_POINT_GROUP = "agents.workloads"


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
        WorkloadNotFound: The package directory does not exist.
        ManifestNotFound: manifest.yaml is missing.
        ContractNotFound: contract.py is missing or does not export a
            Contract.
        WorkloadValidationError: manifest.yaml is not valid YAML, is not
            a mapping, fails Pydantic validation, declares a `name` that
            does not match the package directory (BL-010), or references
            a skill absent from `registry` (BL-011).
        ImportError: The package, contract.py, or __main__.py exists but
            failed to import (e.g. a missing dependency). The original
            exception propagates unmodified so the real failure is not
            masked as "not found".
    """
    try:
        pkg = importlib.import_module(f"workloads.{name}")
    except ModuleNotFoundError as exc:
        # The workload package itself being absent is "not found"; a
        # ModuleNotFoundError naming some *other* module means the
        # package exists but its __init__ failed to import a
        # dependency -- surface that real error, do not mislabel it.
        if exc.name in (f"workloads.{name}", "workloads", None):
            raise WorkloadNotFound(name, str(exc)) from exc
        raise
    except ImportError:
        # cannot-import-name / circular import inside the package: a
        # real failure, not "not found". Propagate for honest triage.
        raise

    pkg_file = getattr(pkg, "__file__", None)
    if pkg_file is None:
        raise WorkloadNotFound(name, "package has no __file__")
    package_path = Path(pkg_file).parent

    def _import(submodule: str) -> ModuleType | None:
        """Import workloads.<name>.<submodule>.

        Returns None only when the submodule is genuinely absent; a
        real import failure (missing dependency, syntax error) is
        propagated, not swallowed.
        """
        target = f"workloads.{name}.{submodule}"
        try:
            return importlib.import_module(target)
        except ModuleNotFoundError as exc:
            if exc.name == target:
                return None
            raise

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

    Security: importing the bundle executes its Python (``contract.py``
    at load, ``__main__.py`` module-level code). A workload is trusted
    code by contract; unlike skill install (ADR 0008) there is no
    ``allow_contract`` gate here. Only load directories you trust. See
    ``SECURITY.md`` and ``LIMITATIONS.md`` L14.

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
            return None  # genuinely absent
        mod_name = f"_oot_workload_{name}_{submodule}".replace("-", "_")
        spec = importlib.util.spec_from_file_location(mod_name, file)
        if spec is None or spec.loader is None:
            # The file exists but a loader could not be built: a real
            # setup failure, not "absent". Surface it rather than let
            # it be misreported as "contract.py is missing".
            raise ImportError(f"cannot build import spec for {file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _build_loaded_workload(name, package_path, _import, registry)


def load_workload_from_entry_point(
    name: str,
    *,
    group: str = _ENTRY_POINT_GROUP,
    registry: SkillRegistry | None = None,
) -> LoadedWorkload:
    """Load a workload from an installed package's entry point (BL-121).

    Extends out-of-tree loading beyond a filesystem path: a third-party
    package can ship a workload by declaring
    ``[project.entry-points."agents.workloads"]`` with
    ``<name> = "<importable.package>"``. The entry point resolves to a
    package whose directory holds ``manifest.yaml`` / ``contract.py`` /
    optional ``__main__.py``; the same BL-010 / BL-011 validators apply.

    Security: like ``load_workload_from_path`` (and unlike skill
    install), this imports and executes the target package's Python. A
    workload is trusted code by contract; only enable workload packages
    you trust. See ``SECURITY.md`` and ``LIMITATIONS.md`` L14.

    Args:
        name: The entry-point name to resolve.
        group: The entry-point group (default ``agents.workloads``).
        registry: Optional SkillRegistry for the BL-011 skills check.

    Returns:
        LoadedWorkload with manifest, contract, package_path, optional main.

    Raises:
        WorkloadNotFound: No entry point with that name in the group, or
            its target module has no resolvable file location.
        ManifestNotFound / ContractNotFound / WorkloadValidationError:
            As for the other loaders.
        ImportError: The entry-point target failed to import (the
            original error propagates, not masked as "not found").
    """
    eps = importlib.metadata.entry_points(group=group)
    match = next((ep for ep in eps if ep.name == name), None)
    if match is None:
        raise WorkloadNotFound(name, f"no entry point {name!r} in group {group!r}")
    try:
        module = importlib.import_module(match.value)
    except ModuleNotFoundError as exc:
        # For a dotted target ``foo.bar``, a missing ``foo`` reports
        # exc.name == "foo" (the top-level package), not the full
        # target. The target itself being absent (the missing module is
        # the target or an ancestor package of it) is "not found"; a
        # missing module that is NOT an ancestor means the target
        # imported but a dependency is absent: a real ImportError to
        # surface for honest triage, not mislabel as "not found"
        # (parity with the in-tree load_workload).
        missing = exc.name
        if missing is None or match.value == missing or match.value.startswith(missing + "."):
            raise WorkloadNotFound(name, f"entry-point target {match.value!r}: {exc}") from exc
        raise

    mod_file = getattr(module, "__file__", None)
    if mod_file is None:
        raise WorkloadNotFound(name, f"entry-point target {match.value!r} has no __file__")
    package_path = Path(mod_file).parent

    def _import(submodule: str) -> ModuleType | None:
        target = f"{match.value}.{submodule}"
        try:
            return importlib.import_module(target)
        except ModuleNotFoundError as exc:
            if exc.name == target:
                return None
            raise

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
    # Use registry.get (resolves bare names AND the BL-053 name@version
    # form); plain `in` only matches bare names.
    if registry is not None:
        missing = [s for s in manifest.skills if registry.get(s) is None]
        if missing:
            raise WorkloadValidationError(
                name,
                f"skills not found in registry: {', '.join(sorted(missing))}",
            )

    # A genuinely-absent contract.py is ContractNotFound; a real import
    # failure inside it (missing dep, syntax error) propagates as the
    # original exception for honest triage rather than being relabelled.
    contract_mod = import_submodule("contract")
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
