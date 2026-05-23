"""BL-217 (seventh audit, ADR 0017): SubprocessSkillContractExecutor metadata validation.

The parent receives JSON metadata frames from the child describing the
contract's predicates: each item should be ``{"name": str, "severity":
str}`` where the severity string is a member of the ``Severity``
StrEnum. Without validation, a malformed item (missing ``"name"``,
non-string ``"severity"``, unknown severity value) leaks the
underlying ``KeyError`` / ``ValueError`` past the documented
``SkillContractExecutorError`` boundary that callers depend on.

These tests exercise the parent-side validator (the ``_proxies``
closure in ``SubprocessSkillContractExecutor.load``) directly via a
stub evaluator so the test runs without spawning a subprocess and
stays deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from skills.execution import (
    SkillContractExecutorError,
    SubprocessSkillContractExecutor,
)
from skills.loader import discover_skill


def _write_skill(path: Path, contract_body: str) -> None:
    (path / "SKILL.md").write_text(
        f"---\nname: {path.name}\ndescription: bl217 test skill\n---\nbody\n",
        encoding="utf-8",
    )
    (path / "contract.py").write_text(contract_body, encoding="utf-8")


class _StubEvaluator:
    """A fake ``_SubprocessEvaluator`` returning a caller-supplied
    metadata dict, so the parent-side validator runs against an
    adversarial child response without spawning a subprocess.
    """

    def __init__(self, meta: dict[str, Any]) -> None:
        self._meta = meta
        self.closed = False

    def load_metadata(self) -> dict[str, Any]:
        return self._meta

    def close(self) -> None:
        self.closed = True

    def evaluate(self, _slot: str, _name: str, _state: object) -> bool:
        return True


def _load_with_stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, meta: dict[str, Any]) -> Any:
    """Run ``SubprocessSkillContractExecutor.load`` with the evaluator
    factory swapped for one that returns the supplied metadata.
    """
    # ``discover_skill`` requires the directory name to match the
    # manifest name; use ``t`` for both.
    skill_dir = tmp_path / "t"
    skill_dir.mkdir()
    _write_skill(
        skill_dir,
        "from harness.contract import Contract\n"
        "contract = Contract(name='t', version='1', preconditions=[], invariants=[],"
        " postconditions=[], governance=[])\n",
    )
    skill = discover_skill(skill_dir)
    monkeypatch.setattr(
        "skills.execution._SubprocessEvaluator",
        lambda *args, **kwargs: _StubEvaluator(meta),  # type: ignore[arg-type]
    )
    executor = SubprocessSkillContractExecutor()
    return executor.load(skill)


def test_metadata_missing_name_key_translated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta = {
        "name": "t",
        "version": "1",
        "preconditions": [{"severity": "hard"}],
        "invariants": [],
        "postconditions": [],
        "governance": [],
    }
    with pytest.raises(SkillContractExecutorError, match="missing key"):
        _load_with_stub(monkeypatch, tmp_path, meta)


def test_metadata_unknown_severity_translated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta = {
        "name": "t",
        "version": "1",
        "preconditions": [{"name": "p1", "severity": "MEDIUM"}],
        "invariants": [],
        "postconditions": [],
        "governance": [],
    }
    with pytest.raises(SkillContractExecutorError, match="unknown severity"):
        _load_with_stub(monkeypatch, tmp_path, meta)


def test_metadata_non_dict_item_translated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    meta = {
        "name": "t",
        "version": "1",
        "preconditions": ["not_a_dict"],
        "invariants": [],
        "postconditions": [],
        "governance": [],
    }
    with pytest.raises(SkillContractExecutorError, match="expected dict"):
        _load_with_stub(monkeypatch, tmp_path, meta)


def test_metadata_non_string_name_translated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta = {
        "name": "t",
        "version": "1",
        "preconditions": [{"name": 42, "severity": "hard"}],
        "invariants": [],
        "postconditions": [],
        "governance": [],
    }
    with pytest.raises(SkillContractExecutorError, match="name/severity must be str"):
        _load_with_stub(monkeypatch, tmp_path, meta)


def test_metadata_valid_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Sanity: a well-formed metadata frame still loads cleanly.
    meta = {
        "name": "t",
        "version": "1",
        "preconditions": [{"name": "p1", "severity": "hard"}],
        "invariants": [],
        "postconditions": [],
        "governance": [],
    }
    contract = _load_with_stub(monkeypatch, tmp_path, meta)
    assert contract is not None
    assert contract.name == "t"
    assert len(contract.preconditions) == 1
    assert contract.preconditions[0].name == "p1"
