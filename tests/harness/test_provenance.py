"""Tests for harness.provenance (BL-185, ADR 0012)."""

from __future__ import annotations

from pydantic import BaseModel

from harness.contract import Contract, Severity, predicate
from harness.provenance import (
    RUN_RECORD_SCHEMA_VERSION,
    RunRecord,
    contract_digest,
    verify_run_record,
)


class _In(BaseModel):
    q: str


class _Out(BaseModel):
    t: str


@predicate(name="non_empty", severity=Severity.HARD)
def _non_empty(s: _In) -> bool:
    return bool(s.q)


@predicate(name="has_text", severity=Severity.SOFT)
def _has_text(s: _Out) -> bool:
    return bool(s.t)


def _contract(**kw: object) -> Contract[_In, _Out]:
    base: dict[str, object] = {
        "name": "wl",
        "version": "1.0.0",
        "preconditions": [_non_empty],
        "postconditions": [_has_text],
    }
    base.update(kw)
    return Contract(**base)  # type: ignore[arg-type]


def test_digest_is_deterministic() -> None:
    assert contract_digest(_contract()) == contract_digest(_contract())


def test_digest_is_predicate_order_independent() -> None:
    @predicate(name="a", severity=Severity.HARD)
    def _a(s: _In) -> bool:
        return True

    @predicate(name="b", severity=Severity.HARD)
    def _b(s: _In) -> bool:
        return True

    one = _contract(preconditions=[_a, _b])
    two = _contract(preconditions=[_b, _a])
    assert contract_digest(one) == contract_digest(two)


def test_digest_changes_with_severity_and_identity() -> None:
    @predicate(name="non_empty", severity=Severity.SOFT)
    def _soft(s: _In) -> bool:
        return bool(s.q)

    assert contract_digest(_contract()) != contract_digest(_contract(preconditions=[_soft]))
    assert contract_digest(_contract()) != contract_digest(_contract(version="2.0.0"))
    assert contract_digest(_contract()) != contract_digest(
        _contract(approval_required=["send_email"])
    )


def _record(contract: Contract[_In, _Out], **kw: object) -> RunRecord:
    base: dict[str, object] = {
        "run_id": "trace123",
        "workload": contract.name,
        "contract_name": contract.name,
        "contract_version": contract.version,
        "contract_digest": contract_digest(contract),
        "outcome": "completed",
        "started_at": "2026-05-17T00:00:00+00:00",
        "completed_at": "2026-05-17T00:00:01+00:00",
        "duration_ms": 1000.0,
    }
    base.update(kw)
    return RunRecord(**base)  # type: ignore[arg-type]


def test_verify_clean_record_passes() -> None:
    c = _contract()
    assert verify_run_record(_record(c), c) == []
    assert _record(c).schema_version == RUN_RECORD_SCHEMA_VERSION


def test_verify_flags_digest_mismatch() -> None:
    c = _contract()
    bad = _record(c, contract_digest="0" * 64)
    errs = verify_run_record(bad, c)
    assert len(errs) == 1
    assert "does not match the live digest" in errs[0]


def test_verify_flags_identity_and_version() -> None:
    c = _contract()
    errs = verify_run_record(_record(c, contract_name="other", contract_version="9.9.9"), c)
    assert any("contract_name" in e for e in errs)
    assert any("contract_version" in e for e in errs)


def test_verify_flags_unsupported_schema_version() -> None:
    c = _contract()
    errs = verify_run_record(_record(c, schema_version="0.0.1"), c)
    assert any("schema_version" in e for e in errs)
