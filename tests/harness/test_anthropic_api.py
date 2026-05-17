"""Tests for harness.anthropic_api (BL-186, ADR 0012)."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest

from harness.anthropic_api import (
    DEFAULT_MODEL,
    AnthropicBatchProcessor,
    BatchRequest,
    cache_control_system,
)


def test_cache_control_default_ttl() -> None:
    blocks = cache_control_system("frozen prefix")
    assert blocks == [
        {
            "type": "text",
            "text": "frozen prefix",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_cache_control_one_hour_ttl() -> None:
    blocks = cache_control_system("ctx", ttl="1h")
    assert blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_batch_request_to_wire_minimal_and_full() -> None:
    minimal = BatchRequest(custom_id="a", messages=[{"role": "user", "content": "hi"}])
    assert minimal.model == DEFAULT_MODEL
    wire = minimal.to_wire()
    assert wire["custom_id"] == "a"
    assert "system" not in wire["params"]
    assert "thinking" not in wire["params"]

    full = BatchRequest(
        custom_id="b",
        messages=[{"role": "user", "content": "q"}],
        model="claude-haiku-4-5",
        max_tokens=50,
        system="be terse",
        thinking={"type": "adaptive"},
    )
    p = full.to_wire()["params"]
    assert p["model"] == "claude-haiku-4-5"
    assert p["system"] == "be terse"
    assert p["thinking"] == {"type": "adaptive"}


class _Block:
    def __init__(self, type_: str, text: str | None = None) -> None:
        self.type = type_
        self.text = text


class _Msg:
    def __init__(self, blocks: list[_Block]) -> None:
        self.content = blocks


class _Result:
    def __init__(self, type_: str, message: Any = None, error: Any = None) -> None:
        self.type = type_
        self.message = message
        self.error = error


class _Entry:
    def __init__(self, custom_id: str, result: _Result) -> None:
        self.custom_id = custom_id
        self.result = result


class _Counts:
    def __init__(self, succeeded: int, errored: int, processing: int) -> None:
        self.succeeded = succeeded
        self.errored = errored
        self.processing = processing


class _Batch:
    def __init__(self, id_: str, status: str, counts: _Counts | None = None) -> None:
        self.id = id_
        self.processing_status = status
        self.request_counts = counts


class _FakeBatches:
    def __init__(self) -> None:
        self.created_with: list[dict[str, Any]] | None = None

    def create(self, *, requests: list[dict[str, Any]]) -> _Batch:
        self.created_with = requests
        return _Batch("batch_1", "in_progress")

    def retrieve(self, batch_id: str, /) -> _Batch:
        return _Batch(batch_id, "ended", _Counts(2, 1, 0))

    def results(self, batch_id: str, /) -> Iterator[_Entry]:
        yield _Entry("ok", _Result("succeeded", _Msg([_Block("text", "hello ")])))
        yield _Entry(
            "ok2",
            _Result("succeeded", _Msg([_Block("thinking"), _Block("text", "world")])),
        )
        yield _Entry("bad", _Result("errored", error=types.SimpleNamespace(type="invalid_request")))
        yield _Entry("gone", _Result("expired"))

    def cancel(self, batch_id: str, /) -> _Batch:
        return _Batch(batch_id, "canceling")


def test_submit_rejects_empty() -> None:
    proc = AnthropicBatchProcessor(_FakeBatches())
    with pytest.raises(ValueError, match="at least one"):
        proc.submit([])


def test_submit_status_results_cancel_roundtrip() -> None:
    fake = _FakeBatches()
    proc = AnthropicBatchProcessor(fake)

    bid = proc.submit([BatchRequest(custom_id="ok", messages=[{"role": "user", "content": "x"}])])
    assert bid == "batch_1"
    assert fake.created_with is not None
    assert fake.created_with[0]["custom_id"] == "ok"

    st = proc.status(bid)
    assert st.ended is True
    assert (st.succeeded, st.errored, st.processing) == (2, 1, 0)

    results = list(proc.results(bid))
    by_id = {r.custom_id: r for r in results}
    assert by_id["ok"].text == "hello "
    assert by_id["ok2"].text == "world"  # non-text blocks skipped
    assert by_id["bad"].type == "errored"
    assert by_id["bad"].error_type == "invalid_request"
    assert by_id["gone"].type == "expired"
    assert by_id["gone"].text is None

    assert proc.cancel(bid) == "canceling"


def test_status_handles_missing_counts() -> None:
    class _NoCounts(_FakeBatches):
        def retrieve(self, batch_id: str, /) -> _Batch:
            return _Batch(batch_id, "in_progress", None)

    st = AnthropicBatchProcessor(_NoCounts()).status("b")
    assert st.ended is False
    assert (st.succeeded, st.errored, st.processing) == (0, 0, 0)


def test_from_env_uses_lazily_imported_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs
            self.messages = types.SimpleNamespace(batches=_FakeBatches())

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    proc = AnthropicBatchProcessor.from_env(api_key="sk-test")
    assert isinstance(proc, AnthropicBatchProcessor)
    assert captured["kwargs"] == {"api_key": "sk-test"}

    AnthropicBatchProcessor.from_env()
    assert captured["kwargs"] == {}
