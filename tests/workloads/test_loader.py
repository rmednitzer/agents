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


_MIN_MANIFEST = (
    "name: {name}\n"
    "version: 0.1.0\n"
    "description: d\n"
    "runtime:\n"
    "  adapter: in-process-stub\n"
    "  model: none\n"
)

_CONTRACT_PY = (
    "from pydantic import BaseModel\n"
    "from harness import Contract\n"
    "class I(BaseModel):\n    x: str\n"
    "class O(BaseModel):\n    y: str\n"
    'contract: Contract[I, O] = Contract(name="{name}", version="0.1.0")\n'
)


def _full_workload(root: Path, name: str, manifest_text: str) -> Iterator[str]:
    """Materialize a temp workload with manifest + contract, importable."""
    pkg_dir = root / name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "manifest.yaml").write_text(manifest_text)
    (pkg_dir / "contract.py").write_text(_CONTRACT_PY.format(name=name))
    workloads.__path__.append(str(root))
    importlib.invalidate_caches()
    try:
        yield name
    finally:
        workloads.__path__.remove(str(root))
        for mod in (f"workloads.{name}", f"workloads.{name}.contract"):
            sys.modules.pop(mod, None)
        importlib.invalidate_caches()


def test_bl010_name_must_match_directory(tmp_path: Path) -> None:
    """BL-010: a manifest name that differs from the package dir is rejected."""
    gen = _temp_workload(tmp_path, "_dir_name_x", _MIN_MANIFEST.format(name="not_dir_name_x"))
    name = next(gen)
    try:
        with pytest.raises(WorkloadValidationError, match="does not match package"):
            load_workload(name)
    finally:
        next(gen, None)


def test_bl010_example_workload_name_matches() -> None:
    """The in-tree _example bundle satisfies the directory-name validator."""
    lw = load_workload("_example")
    assert lw.manifest.name == lw.package_path.name == "_example"


def test_bl011_unresolved_skill_rejected(tmp_path: Path) -> None:
    """BL-011: a skill absent from the supplied registry fails the load."""
    from skills.registry import SkillRegistry
    from skills.types import Skill, SkillManifest

    gen = _full_workload(
        tmp_path,
        "_wl_skillcheck",
        _MIN_MANIFEST.format(name="_wl_skillcheck") + "skills: [present, absent]\n",
    )
    name = next(gen)
    try:
        reg = SkillRegistry()
        reg.add(
            Skill(
                manifest=SkillManifest(name="present", description="d"),
                path=tmp_path / "present",
            )
        )
        with pytest.raises(WorkloadValidationError, match="absent"):
            load_workload(name, registry=reg)
        # No registry => skill resolution is not checked.
        lw = load_workload(name)
        assert lw.manifest.skills == ["present", "absent"]
    finally:
        next(gen, None)


def test_bl011_all_skills_resolved(tmp_path: Path) -> None:
    """BL-011: load succeeds when every declared skill resolves."""
    from skills.registry import SkillRegistry
    from skills.types import Skill, SkillManifest

    gen = _full_workload(
        tmp_path,
        "_wl_skillok",
        _MIN_MANIFEST.format(name="_wl_skillok") + "skills: [a, b]\n",
    )
    name = next(gen)
    try:
        reg = SkillRegistry()
        for sn in ("a", "b"):
            reg.add(
                Skill(
                    manifest=SkillManifest(name=sn, description="d"),
                    path=tmp_path / sn,
                )
            )
        lw = load_workload(name, registry=reg)
        assert lw.manifest.skills == ["a", "b"]
    finally:
        next(gen, None)
