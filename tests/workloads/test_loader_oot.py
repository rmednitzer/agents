"""Tests for out-of-tree workload loading (BL-090)."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.contract import Contract
from workloads.errors import (
    ContractNotFound,
    ManifestNotFound,
    WorkloadNotFound,
    WorkloadValidationError,
)
from workloads.loader import LoadedWorkload, load_workload_from_path

_MANIFEST = (
    "name: {name}\n"
    "version: 0.1.0\n"
    "description: An out-of-tree workload.\n"
    "runtime:\n"
    "  adapter: in-process-stub\n"
    "  model: none\n"
)
_CONTRACT = (
    "from pydantic import BaseModel\n"
    "from harness import Contract\n"
    "class I(BaseModel):\n    x: str\n"
    "class O(BaseModel):\n    y: str\n"
    'contract: Contract[I, O] = Contract(name="{name}", version="0.1.0")\n'
)
_MAIN = "async def main(q: str) -> str:\n    return q.upper()\n"


def _bundle(root: Path, name: str, *, manifest_name: str | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(_MANIFEST.format(name=manifest_name or name))
    (d / "contract.py").write_text(_CONTRACT.format(name=name))
    (d / "__main__.py").write_text(_MAIN)
    return d


def test_loads_arbitrary_path(tmp_path: Path) -> None:
    d = _bundle(tmp_path / "anywhere", "ext_wl")
    lw = load_workload_from_path(d)
    assert isinstance(lw, LoadedWorkload)
    assert lw.manifest.name == "ext_wl"
    assert isinstance(lw.contract, Contract)
    assert lw.main is not None
    assert lw.package_path == d.resolve()


def test_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(WorkloadNotFound):
        load_workload_from_path(tmp_path / "nope")


def test_missing_manifest_raises(tmp_path: Path) -> None:
    d = tmp_path / "incomplete"
    d.mkdir()
    with pytest.raises(ManifestNotFound):
        load_workload_from_path(d)


def test_missing_contract_raises(tmp_path: Path) -> None:
    d = tmp_path / "nocontract"
    d.mkdir()
    (d / "manifest.yaml").write_text(_MANIFEST.format(name="nocontract"))
    with pytest.raises(ContractNotFound):
        load_workload_from_path(d)


def test_bl010_applies_out_of_tree(tmp_path: Path) -> None:
    d = _bundle(tmp_path / "root", "dir_name", manifest_name="other_name")
    with pytest.raises(WorkloadValidationError, match="does not match"):
        load_workload_from_path(d)


def test_bl011_applies_out_of_tree(tmp_path: Path) -> None:
    from skills.registry import SkillRegistry
    from skills.types import Skill, SkillManifest

    d = _bundle(tmp_path / "r2", "skilled")
    (d / "manifest.yaml").write_text(
        _MANIFEST.format(name="skilled") + "skills: [present, missing]\n"
    )
    reg = SkillRegistry()
    reg.add(
        Skill(
            manifest=SkillManifest(name="present", description="d"),
            path=tmp_path / "present",
        )
    )
    with pytest.raises(WorkloadValidationError, match="missing"):
        load_workload_from_path(d, registry=reg)


@pytest.mark.asyncio
async def test_out_of_tree_main_is_runnable(tmp_path: Path) -> None:
    d = _bundle(tmp_path / "runnable", "runme")
    lw = load_workload_from_path(d)
    assert lw.main is not None
    assert await lw.main("hi") == "HI"
