"""Tests for harness.openai_api (BL-187, ADR 0012)."""

from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from harness.openai_api import (
    OpenAIBatchProcessor,
    OpenAIBatchRequest,
)


def test_to_jsonl_line_minimal_and_full() -> None:
    minimal = OpenAIBatchRequest(
        custom_id="a", model="some-model", messages=[{"role": "user", "content": "hi"}]
    )
    line = json.loads(minimal.to_jsonl_line())
    assert line["custom_id"] == "a"
    assert line["method"] == "POST"
    assert line["url"] == "/v1/chat/completions"
    assert line["body"] == {"model": "some-model", "messages": [{"role": "user", "content": "hi"}]}
    assert "max_tokens" not in line["body"]

    full = OpenAIBatchRequest(
        custom_id="b",
        model="some-model",
        messages=[{"role": "user", "content": "q"}],
        max_tokens=64,
    )
    assert json.loads(full.to_jsonl_line())["body"]["max_tokens"] == 64


def test_model_is_required() -> None:
    with pytest.raises(ValueError, match="model"):
        OpenAIBatchRequest(custom_id="a", messages=[])  # type: ignore[call-arg]


class _Obj:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _TextContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _BytesContent:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def read(self) -> bytes:
        return self._raw


class _FakeFiles:
    def __init__(self, files: dict[str, Any]) -> None:
        self._files = files
        self.created_with: Any = None

    def create(self, *, file: Any, purpose: str) -> _Obj:
        self.created_with = (file, purpose)
        return _Obj(id="file_in")

    def content(self, file_id: str, /) -> Any:
        return self._files[file_id]


class _FakeBatches:
    def __init__(self, batch: _Obj) -> None:
        self._batch = batch
        self.created_with: Any = None

    def create(self, *, input_file_id: str, endpoint: str, completion_window: str) -> _Obj:
        self.created_with = (input_file_id, endpoint, completion_window)
        return _Obj(id="batch_1", status="validating")

    def retrieve(self, batch_id: str, /) -> _Obj:
        return self._batch

    def cancel(self, batch_id: str, /) -> _Obj:
        return _Obj(id=batch_id, status="cancelling")


class _FakeClient:
    def __init__(self, batch: _Obj, files: dict[str, Any]) -> None:
        self.files = _FakeFiles(files)
        self.batches = _FakeBatches(batch)


def _ok_line(cid: str, content: str) -> str:
    return json.dumps(
        {
            "custom_id": cid,
            "response": {
                "status_code": 200,
                "body": {"choices": [{"message": {"content": content}}]},
            },
        }
    )


def test_submit_builds_jsonl_and_returns_batch_id() -> None:
    client = _FakeClient(_Obj(id="b", status="validating"), {})
    proc = OpenAIBatchProcessor(client)
    bid = proc.submit(
        [
            OpenAIBatchRequest(
                custom_id="x", model="m", messages=[{"role": "user", "content": "1"}]
            ),
            OpenAIBatchRequest(
                custom_id="y", model="m", messages=[{"role": "user", "content": "2"}]
            ),
        ]
    )
    assert bid == "batch_1"
    sent_file, purpose = client.files.created_with
    assert purpose == "batch"
    payload = sent_file[1].getvalue().decode("utf-8")
    assert [json.loads(line_)["custom_id"] for line_ in payload.splitlines()] == ["x", "y"]
    assert client.batches.created_with == ("file_in", "/v1/chat/completions", "24h")


def test_submit_rejects_empty() -> None:
    proc = OpenAIBatchProcessor(_FakeClient(_Obj(id="b", status="x"), {}))
    with pytest.raises(ValueError, match="at least one"):
        proc.submit([])


def test_status_and_ended() -> None:
    batch = _Obj(id="b", status="completed", request_counts=_Obj(completed=3, failed=1, total=4))
    st = OpenAIBatchProcessor(_FakeClient(batch, {})).status("b")
    assert st.ended is True
    assert (st.completed, st.failed, st.total) == (3, 1, 4)

    running = _Obj(id="b", status="in_progress", request_counts=None)
    st2 = OpenAIBatchProcessor(_FakeClient(running, {})).status("b")
    assert st2.ended is False
    assert (st2.completed, st2.failed, st2.total) == (0, 0, 0)


def test_results_from_output_file_text() -> None:
    body = "\n".join(
        [
            _ok_line("ok", "hello"),
            json.dumps({"custom_id": "bad", "response": {"status_code": 400, "body": {}}}),
            "",  # blank line skipped
        ]
    )
    batch = _Obj(id="b", status="completed", output_file_id="out", error_file_id=None)
    client = _FakeClient(batch, {"out": _TextContent(body)})
    by_id = {r.custom_id: r for r in OpenAIBatchProcessor(client).results("b")}
    assert by_id["ok"].type == "succeeded"
    assert by_id["ok"].text == "hello"
    assert by_id["bad"].type == "errored"
    assert by_id["bad"].error_type == "http_400"


def test_results_fall_back_to_error_file_bytes() -> None:
    err = json.dumps({"custom_id": "e", "error": {"code": "rate_limit_exceeded"}}).encode()
    batch = _Obj(id="b", status="failed", output_file_id=None, error_file_id="errf")
    client = _FakeClient(batch, {"errf": _BytesContent(err)})
    results = list(OpenAIBatchProcessor(client).results("b"))
    assert len(results) == 1
    assert results[0].type == "errored"
    assert results[0].error_type == "rate_limit_exceeded"


def test_results_combine_output_and_error_files() -> None:
    out = _ok_line("good", "hi")
    err = json.dumps({"custom_id": "bad", "error": {"code": "context_length_exceeded"}})
    batch = _Obj(id="b", status="completed", output_file_id="out", error_file_id="errf")
    client = _FakeClient(batch, {"out": _TextContent(out), "errf": _TextContent(err)})
    by_id = {r.custom_id: r for r in OpenAIBatchProcessor(client).results("b")}
    assert set(by_id) == {"good", "bad"}  # error rows not dropped
    assert by_id["good"].type == "succeeded"
    assert by_id["bad"].type == "errored"
    assert by_id["bad"].error_type == "context_length_exceeded"


def test_output_file_null_response_uses_structured_error_code() -> None:
    """BL-189: a request-level failure can land in the *output* file with
    ``response: null`` and a structured ``error`` (distinct from the
    error-file rows). The processor must surface that error code, not a
    diagnostically-useless ``http_None``.
    """
    body = "\n".join(
        [
            _ok_line("ok", "hello"),
            json.dumps(
                {
                    "custom_id": "internal",
                    "response": None,
                    "error": {"code": "internal_error", "message": "boom"},
                }
            ),
            # response absent entirely + structured error.
            json.dumps({"custom_id": "absent", "error": {"code": "server_error"}}),
            # No response and no error: still a deterministic label.
            json.dumps({"custom_id": "blank"}),
        ]
    )
    batch = _Obj(id="b", status="completed", output_file_id="out", error_file_id=None)
    client = _FakeClient(batch, {"out": _TextContent(body)})
    by_id = {r.custom_id: r for r in OpenAIBatchProcessor(client).results("b")}
    assert by_id["ok"].type == "succeeded"
    assert by_id["internal"].type == "errored"
    assert by_id["internal"].error_type == "internal_error"
    assert by_id["absent"].error_type == "server_error"
    # No structured error: falls back to the http_<status> label, never
    # crashes or drops the row.
    assert by_id["blank"].type == "errored"
    assert by_id["blank"].error_type == "http_None"


def test_results_empty_when_no_files() -> None:
    batch = _Obj(id="b", status="expired", output_file_id=None, error_file_id=None)
    assert list(OpenAIBatchProcessor(_FakeClient(batch, {})).results("b")) == []


def test_cancel_returns_status() -> None:
    proc = OpenAIBatchProcessor(_FakeClient(_Obj(id="b", status="x"), {}))
    assert proc.cancel("b") == "cancelling"


def test_from_env_uses_lazily_imported_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _SDKClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs
            self.files = _FakeFiles({})
            self.batches = _FakeBatches(_Obj(id="b", status="x"))

    fake_module = types.ModuleType("openai")
    fake_module.OpenAI = _SDKClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    proc = OpenAIBatchProcessor.from_env(api_key="sk-x")
    assert isinstance(proc, OpenAIBatchProcessor)
    assert captured["kwargs"] == {"api_key": "sk-x"}

    OpenAIBatchProcessor.from_env()
    assert captured["kwargs"] == {}
