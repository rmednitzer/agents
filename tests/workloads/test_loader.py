"""Tests for workloads.loader."""

from __future__ import annotations

import pytest

from harness.contract import Contract
from workloads.errors import (
    ContractNotFound,
    ManifestNotFound,
    WorkloadNotFound,
    WorkloadValidationError,
)
from workloads.loader import LoadedWorkload, load_workload


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
