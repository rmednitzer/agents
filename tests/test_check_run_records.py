"""Robustness of the scripts/check_run_records.py offline gate (ADR 0012).

Regression coverage for the malformed-input guards: a *.run.json that
is valid JSON but not an object, and a --registry payload that is valid
JSON but not a mapping, must both produce a deterministic, documented
outcome instead of an AttributeError traceback.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from harness.provenance import RunRecord

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


@pytest.mark.parametrize(
    "value",
    [
        123,  # JSON number
        None,  # explicit null
        ["a" * 64],  # list
        "A" * 64,  # uppercase hex (model normalises to lowercase)
        "z" * 64,  # right length, non-hex
        "abc",  # too short
    ],
)
def test_non_canonical_registry_value_is_invocation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """BL-192: a registry value that is not a canonical lowercase
    64-hex digest makes the gate silently unsatisfiable (it can never
    equal a model-normalised digest). It must be a clear invocation
    failure (exit 2) naming the bad key, not a confusing per-record
    'does not match the registry digest 123' message.
    """
    (tmp_path / "a.run.json").write_text("{}")
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"wl@1.0.0": value}))
    monkeypatch.setattr(
        "sys.argv",
        ["check_run_records", str(tmp_path), "--registry", str(reg)],
    )
    assert crr.main() == 2


def test_canonical_registry_value_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed lowercase 64-hex registry still passes the gate."""
    _write_record(tmp_path / "a.run.json")
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"wl@1.0.0": "b" * 64}))
    monkeypatch.setattr(
        "sys.argv",
        ["check_run_records", str(tmp_path), "--registry", str(reg)],
    )
    # 'b'*64 is canonical, so we get past the registry gate; the record
    # then fails on the digest *content* mismatch (a hard error, exit 1)
    # -- proving the canonical value was accepted, not rejected at parse.
    assert crr.main() == 1
    monkeypatch.setattr("sys.argv", ["check_run_records", str(tmp_path)])
    assert crr.main() == 0


_DIGEST = "a" * 64


def _write_record(path: Path, **overrides: object) -> None:
    fields: dict[str, object] = {
        "run_id": "trace1",
        "workload": "wl",
        "contract_name": "wl",
        "contract_version": "1.0.0",
        "contract_digest": _DIGEST,
        "outcome": "completed",
        "started_at": "2026-05-17T00:00:00+00:00",
        "completed_at": "2026-05-17T00:00:01+00:00",
        "duration_ms": 1000.0,
    }
    fields.update(overrides)
    path.write_text(RunRecord(**fields).model_dump_json())  # type: ignore[arg-type]


def test_clean_record_passes_with_matching_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_record(tmp_path / "a.run.json")
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"wl@1.0.0": _DIGEST}))
    monkeypatch.setattr("sys.argv", ["check_run_records", str(tmp_path), "--registry", str(reg)])
    assert crr.main() == 0


def test_digest_mismatch_and_unknown_contract_are_hard_errors(tmp_path: Path) -> None:
    _write_record(tmp_path / "mismatch.run.json")
    _write_record(tmp_path / "unknown.run.json", contract_name="other")
    registry = {"wl@1.0.0": "b" * 64}  # different digest; 'other@1.0.0' absent
    errs = crr._check_record(tmp_path / "mismatch.run.json", registry)
    assert any("does not match the registry digest" in e for e in errs)
    errs2 = crr._check_record(tmp_path / "unknown.run.json", registry)
    assert any("not in the registry" in e for e in errs2)


def test_invalid_digest_shape_fails_model_validation(tmp_path: Path) -> None:
    rec = tmp_path / "bad.run.json"
    rec.write_text(
        json.dumps(
            {
                "run_id": "t",
                "workload": "wl",
                "contract_name": "wl",
                "contract_version": "1.0.0",
                "contract_digest": "abc",  # not 64 hex
                "outcome": "completed",
                "started_at": "2026-05-17T00:00:00+00:00",
                "completed_at": "2026-05-17T00:00:01+00:00",
                "duration_ms": 1.0,
            }
        )
    )
    errors = crr._check_record(rec, None)
    assert any("does not validate against RunRecord" in e for e in errors)


def test_naive_timestamp_is_a_utc_violation_not_a_crash(tmp_path: Path) -> None:
    rec = tmp_path / "naive.run.json"
    _write_record(
        rec,
        started_at="2026-05-17T00:00:00",  # naive: no offset
        completed_at="2026-05-17T00:00:01+00:00",
    )
    errors = crr._check_record(rec, None)
    assert any("started_at" in e and "not a UTC instant" in e for e in errors)


def test_non_utc_offset_is_rejected(tmp_path: Path) -> None:
    rec = tmp_path / "offset.run.json"
    _write_record(
        rec,
        started_at="2026-05-17T00:00:00+02:00",
        completed_at="2026-05-17T00:00:01+02:00",
    )
    errors = crr._check_record(rec, None)
    assert sum("not a UTC instant" in e for e in errors) == 2


def test_clean_utc_record_has_no_timestamp_violation(tmp_path: Path) -> None:
    rec = tmp_path / "ok.run.json"
    _write_record(rec)  # default timestamps carry +00:00
    assert crr._check_record(rec, None) == []
