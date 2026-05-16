"""Tests for workloads.loader."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import workloads
from harness.contract import Contract
from workloads.errors import (
    ContractNotFound,
    ManifestNotFound,
    WorkloadNotFound,
    WorkloadValidationError,
)
from workloads.loader import LoadedWorkload, load_workload


def _temp_workload(root: Path, name: str, manifest_text: str) -> Iterator[str]:
    """Materialize a temporary workload package and make it importable.

    Yields the workload name for use with load_workload, then tears down
    the package path entry and any cached modules.
    """
    pkg_dir = root / name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "manifest.yaml").write_text(manifest_text)
    workloads.__path__.append(str(root))
    importlib.invalidate_caches()
    try:
        yield name
    finally:
        workloads.__path__.remove(str(root))
        sys.modules.pop(f"workloads.{name}", None)
        importlib.invalidate_caches()


def test_load_example_workload() -> None:
    lw = load_workload("_example")
    assert isinstance(lw, LoadedWorkload)
    assert lw.manifest.name == "_example"
    assert lw.manifest.version == "0.1.0"
    assert lw.manifest.runtime.adapter == "in-process-stub"


def test_loaded_contract_is_contract() -> None:
    lw = load_workload("_example")
    assert isinstance(lw.contract, Contract)
    assert lw.contract.name == "_example"


def test_loaded_main_is_callable() -> None:
    lw = load_workload("_example")
    assert lw.main is not None
    assert callable(lw.main)


def test_package_path_points_to_directory() -> None:
    lw = load_workload("_example")
    assert lw.package_path.is_dir()
    assert (lw.package_path / "manifest.yaml").is_file()
    assert (lw.package_path / "contract.py").is_file()


def test_unknown_workload_raises_workload_not_found() -> None:
    with pytest.raises(WorkloadNotFound):
        load_workload("_does_not_exist_xyz")


def test_loaded_workload_is_frozen() -> None:
    lw = load_workload("_example")
    try:
        lw.manifest = lw.manifest  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("LoadedWorkload should be frozen")


def test_loaded_workload_errors_are_distinguishable() -> None:
    """Each loader error type carries the workload name and is a WorkloadError subclass."""
    from workloads.errors import WorkloadError

    err1 = WorkloadNotFound("x", "reason")
    err2 = ManifestNotFound("x", "/path/manifest.yaml")
    err3 = ContractNotFound("x", "reason")
    err4 = WorkloadValidationError("x", "reason")

    for err in (err1, err2, err3, err4):
        assert isinstance(err, WorkloadError)
        assert err.name == "x"


def test_malformed_manifest_yaml_raises_workload_validation_error(tmp_path: Path) -> None:
    """A syntactically invalid manifest.yaml surfaces as WorkloadValidationError.

    Regression: previously the raw yaml.YAMLError leaked, violating the
    loader's documented Raises: contract.
    """
    gen = _temp_workload(tmp_path, "_audit_broken_yaml", "name: x\n  bad: : indent\n")
    name = next(gen)
    try:
        with pytest.raises(WorkloadValidationError):
            load_workload(name)
    finally:
        next(gen, None)


def test_non_mapping_manifest_raises_workload_validation_error(tmp_path: Path) -> None:
    """A manifest that parses to a non-mapping is rejected with a clear error."""
    gen = _temp_workload(tmp_path, "_audit_scalar_manifest", "just-a-string\n")
    name = next(gen)
    try:
        with pytest.raises(WorkloadValidationError, match="must be a mapping"):
            load_workload(name)
    finally:
        next(gen, None)
