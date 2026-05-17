"""Tests for installed-package workload loading (BL-121)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from harness.contract import Contract
from workloads.errors import WorkloadNotFound
from workloads.loader import LoadedWorkload, load_workload_from_entry_point

_MANIFEST = (
    "name: {name}\n"
    "version: 0.1.0\n"
    "description: An entry-point workload.\n"
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


def _installed_pkg(root: Path, pkg: str, manifest_name: str) -> None:
    d = root / pkg
    d.mkdir(parents=True)
    (d / "__init__.py").write_text("")
    (d / "manifest.yaml").write_text(_MANIFEST.format(name=manifest_name))
    (d / "contract.py").write_text(_CONTRACT.format(name=manifest_name))


class _EP:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


def test_loads_from_entry_point(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed_pkg(tmp_path, "ep_wl", "ep_wl")
    monkeypatch.syspath_prepend(str(tmp_path))

    def _eps(*, group: str) -> list[_EP]:
        assert group == "agents.workloads"
        return [_EP("ep_wl", "ep_wl")]

    monkeypatch.setattr("importlib.metadata.entry_points", _eps)
    lw = load_workload_from_entry_point("ep_wl")
    assert isinstance(lw, LoadedWorkload)
    assert lw.manifest.name == "ep_wl"
    assert isinstance(lw.contract, Contract)


def test_unknown_entry_point_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("importlib.metadata.entry_points", lambda *, group: [])
    with pytest.raises(WorkloadNotFound, match="no entry point"):
        load_workload_from_entry_point("absent")


def test_entry_point_import_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = tmp_path / "broken_ep"
    d.mkdir()
    (d / "__init__.py").write_text("import a_module_that_does_not_exist_xyz\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(
        "importlib.metadata.entry_points",
        lambda *, group: [_EP("broken_ep", "broken_ep")],
    )
    # A real missing dependency must surface, not be masked as not-found.
    with pytest.raises(ModuleNotFoundError, match="a_module_that_does_not_exist_xyz"):
        load_workload_from_entry_point("broken_ep")


def _cleanup_modules() -> None:
    for m in list(sys.modules):
        if m.startswith(("ep_wl", "broken_ep")):
            del sys.modules[m]


@pytest.fixture(autouse=True)
def _modclean() -> Any:
    yield
    _cleanup_modules()
