"""Robustness of the scripts/check_run_records.py offline gate (ADR 0012).

Regression coverage for the malformed-input guards: a *.run.json that
is valid JSON but not an object, and a --registry payload that is valid
JSON but not a mapping, must both produce a deterministic, documented
outcome instead of an AttributeError traceback.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_run_records.py"
_spec = importlib.util.spec_from_file_location("_check_run_records", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
crr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crr)


def test_non_object_record_is_a_per_file_violation(tmp_path: Path) -> None:
    bad = tmp_path / "x.run.json"
    bad.write_text("[]")
    errors = crr._check_record(bad, None)
    assert len(errors) == 1
    assert "expected a RunRecord object" in errors[0]


def test_non_dict_registry_returns_invocation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.run.json").write_text("{}")
    reg = tmp_path / "reg.json"
    reg.write_text('["not", "a", "mapping"]')
    monkeypatch.setattr(
        "sys.argv",
        ["check_run_records", str(tmp_path), "--registry", str(reg)],
    )
    assert crr.main() == 2


def test_empty_corpus_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["check_run_records", str(tmp_path)])
    assert crr.main() == 0
