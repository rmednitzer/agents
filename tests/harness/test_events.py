"""Tests for harness.events."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from harness.contract import Severity
from harness.events import (
    ContractStarted,
    HarnessEvent,
    PreconditionViolated,
)


def _base_fields() -> dict[str, str]:
    return {
        "workload": "example",
        "contract": "example",
        "contract_version": "0.1.0",
        "trace_id": "trace-123",
        "span_id": "span-456",
    }


def test_contract_started_has_kind_discriminator() -> None:
    e = ContractStarted(timestamp=datetime.now(UTC), **_base_fields())
    assert e.kind == "contract_started"


def test_event_is_frozen() -> None:
    e = ContractStarted(timestamp=datetime.now(UTC), **_base_fields())
    try:
        e.workload = "other"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("event should be frozen")


def test_event_json_round_trip() -> None:
    e = PreconditionViolated(
        timestamp=datetime.now(UTC),
        predicate="non_empty",
        severity=Severity.HARD,
        state_snapshot={"query": "abc"},
        **_base_fields(),
    )
    raw = e.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["kind"] == "precondition_violated"
    assert parsed["severity"] == "hard"
    assert parsed["state_snapshot"] == {"query": "abc"}
    assert parsed["trace_id"] == "trace-123"


def test_optional_parent_span_id_defaults_none() -> None:
    e = ContractStarted(timestamp=datetime.now(UTC), **_base_fields())
    assert e.parent_span_id is None


def test_otel_fields_present_on_all_events() -> None:
    e: HarnessEvent = ContractStarted(timestamp=datetime.now(UTC), **_base_fields())
    assert hasattr(e, "trace_id")
    assert hasattr(e, "span_id")
    assert hasattr(e, "parent_span_id")
