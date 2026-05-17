"""Tests for skill-level contracts (BL-052) + composition (BL-060)."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.composition import compose_contracts
from harness.contract import Contract
from skills.errors import SkillManifestError
from skills.loader import discover_skill
from skills.sources import LocalSkillSource, install_skill

_SKILL_MD = "---\nname: {name}\ndescription: A skill.\n---\nbody\n"
_CONTRACT_PY = (
    'from harness import Contract\ncontract: Contract = Contract(name="{name}", version="0.1.0")\n'
)


def _make_skill(root: Path, name: str, with_contract: bool, contract_src: str | None = None):
    d = root / name
    d.mkdir()
    (d / "SKILL.md").write_text(_SKILL_MD.format(name=name), encoding="utf-8")
    if with_contract:
        src = contract_src if contract_src is not None else _CONTRACT_PY.format(name=name)
        (d / "contract.py").write_text(src, encoding="utf-8")
    return discover_skill(d)


def test_no_contract_returns_none(tmp_path: Path) -> None:
    skill = _make_skill(tmp_path, "plain", with_contract=False)
    assert skill.contract_path is None
    assert skill.contract() is None


def test_contract_loaded_and_cached(tmp_path: Path) -> None:
    skill = _make_skill(tmp_path, "guarded", with_contract=True)
    assert skill.contract_path is not None
    c = skill.contract()
    assert isinstance(c, Contract)
    assert c.name == "guarded"
    assert skill.contract() is c  # cached, same object


def test_invalid_contract_raises(tmp_path: Path) -> None:
    skill = _make_skill(tmp_path, "bad", with_contract=True, contract_src="contract = 123\n")
    with pytest.raises(SkillManifestError, match="not a Contract"):
        skill.contract()


def test_missing_export_raises(tmp_path: Path) -> None:
    skill = _make_skill(tmp_path, "noexport", with_contract=True, contract_src="x = 1\n")
    with pytest.raises(SkillManifestError, match="does not export"):
        skill.contract()


def test_skill_contract_composes_with_workload(tmp_path: Path) -> None:
    skill = _make_skill(tmp_path, "composable", with_contract=True)
    skill_contract = skill.contract()
    assert skill_contract is not None
    workload: Contract[object, object] = Contract(name="wl", version="1")
    composed = compose_contracts("wl+composable", "1", workload, skill_contract)
    assert composed.name == "wl+composable"


def _bundle_with_contract(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(_SKILL_MD.format(name=name), encoding="utf-8")
    (d / "contract.py").write_text(_CONTRACT_PY.format(name=name), encoding="utf-8")
    return d


def test_disallowed_contract_is_refused(tmp_path: Path) -> None:
    _bundle_with_contract(tmp_path, "untrusted")
    skill = discover_skill(tmp_path / "untrusted", allow_contract=False)
    assert skill.contract_path is not None
    with pytest.raises(SkillManifestError, match="contract execution disabled"):
        skill.contract()


def test_disallowed_without_contract_is_none(tmp_path: Path) -> None:
    d = tmp_path / "plain"
    d.mkdir()
    (d / "SKILL.md").write_text(_SKILL_MD.format(name="plain"), encoding="utf-8")
    skill = discover_skill(d, allow_contract=False)
    assert skill.contract() is None


def test_install_skill_default_refuses_contract(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    _bundle_with_contract(registry, "shipped")
    skill = install_skill(LocalSkillSource(registry), "shipped", tmp_path / "installed")
    with pytest.raises(SkillManifestError, match="contract execution disabled"):
        skill.contract()


def test_install_skill_opt_in_executes_contract(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    _bundle_with_contract(registry, "trusted")
    skill = install_skill(
        LocalSkillSource(registry), "trusted", tmp_path / "installed", allow_contract=True
    )
    c = skill.contract()
    assert isinstance(c, Contract)
    assert c.name == "trusted"
