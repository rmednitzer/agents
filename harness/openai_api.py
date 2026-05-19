"""Optional OpenAI Batch API capability for workloads (BL-187, ADR 0012).

The OpenAI counterpart of ``harness.anthropic_api.AnthropicBatchProcessor``:
asynchronous bulk processing at ~50% of standard price for
non-latency-sensitive fan-out (offline evaluation sets, corpus
reclassification, backfills). Model-level OpenAI access already works
through the provider-neutral ``Runtime`` / ``PydanticAIRuntime`` (an
``openai:...`` model string); only the bulk surface needs a wrapper.

This is *not* a copy of the Anthropic wrapper. The OpenAI Batch API has
a genuinely different shape: you upload a JSONL file of request lines,
create a batch referencing that file id, then download an output file
of result lines (no inline request/result objects). There is also no
prompt-cache analogue: OpenAI prompt caching is automatic and has no
``cache_control`` block, so nothing mirrors ``cache_control_system``.

Conventions match the rest of the optional backends (ADR 0007): the
``openai`` SDK is an optional extra, imported lazily; the module
imports and type-checks whether or not the SDK is installed (Protocol
boundary plus an ``openai.*`` mypy override). The client is injected so
the logic is fully testable with a fake; only ``from_env`` touches the
real SDK.

Unlike the Anthropic wrapper, ``OpenAIBatchRequest`` has no default
``model``: this code cannot verify current OpenAI model identifiers
against a trusted source, and guessing one would be a silent
mis-identification, so the caller must pass it explicitly.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator, Sequence
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "OpenAIBatchClient",
    "OpenAIBatchProcessor",
    "OpenAIBatchRequest",
    "OpenAIBatchResult",
    "OpenAIBatchStatus",
]

_CHAT_COMPLETIONS_ENDPOINT = "/v1/chat/completions"
_TERMINAL_STATUSES = frozenset({"completed", "failed", "expired", "cancelled"})


class OpenAIBatchRequest(BaseModel):
    """One request in a batch. ``custom_id`` correlates the result.

    ``model`` is required and not defaulted on purpose (see module
    docstring): an unverified model id must not be guessed here.
    """

    model_config = ConfigDict(frozen=True)

    custom_id: str
    model: str
    messages: list[dict[str, Any]]
    max_tokens: int | None = Field(default=None, gt=0)

    def to_jsonl_line(self) -> str:
        """Render the one-line JSON the Batch input file expects.

        Shape: ``{custom_id, method, url, body}`` where ``body`` is a
        Chat Completions request. Optional fields are omitted when unset
        so the line stays minimal.
        """
        body: dict[str, Any] = {"model": self.model, "messages": self.messages}
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        line = {
            "custom_id": self.custom_id,
            "method": "POST",
            "url": _CHAT_COMPLETIONS_ENDPOINT,
            "body": body,
        }
        return json.dumps(line, separators=(",", ":"))


class OpenAIBatchStatus(BaseModel):
    """Snapshot of a batch's progress."""

    model_config = ConfigDict(frozen=True)

    id: str
    status: str
    completed: int = 0
    failed: int = 0
    total: int = 0

    @property
    def ended(self) -> bool:
        """True once the batch reached a terminal status."""
        return self.status in _TERMINAL_STATUSES


class OpenAIBatchResult(BaseModel):
    """One decoded result line from a completed batch.

    ``type`` is ``succeeded`` when the per-request HTTP status is 200,
    else ``errored``. ``text`` is the first choice's message content on
    success, else None. ``error_type`` carries the API/structural error
    on failure.
    """

    model_config = ConfigDict(frozen=True)

    custom_id: str
    type: str
    text: str | None = None
    error_type: str | None = None


class _OpenAIFilesResource(Protocol):
    def create(self, *, file: Any, purpose: str) -> Any: ...

    def content(self, file_id: str, /) -> Any: ...


class _OpenAIBatchesResource(Protocol):
    def create(self, *, input_file_id: str, endpoint: str, completion_window: str) -> Any: ...

    def retrieve(self, batch_id: str, /) -> Any: ...

    def cancel(self, batch_id: str, /) -> Any: ...


class OpenAIBatchClient(Protocol):
    """The slice of ``openai.OpenAI()`` this module uses.

    Injected so the processor is testable without the SDK. The real
    object is an ``openai.OpenAI`` instance (it exposes ``.files`` and
    ``.batches``).
    """

    @property
    def files(self) -> _OpenAIFilesResource: ...

    @property
    def batches(self) -> _OpenAIBatchesResource: ...


def _text_of(body: Any) -> str | None:
    """First choice's message content from a Chat Completions body."""
    if not isinstance(body, dict):
        return None
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) and content else None


def _error_code(line: dict[str, Any], fallback: str) -> str:
    """Structured error code from a result line, else ``fallback``.

    A row can carry ``error: {...}`` but with ``code`` null, empty, or a
    non-string (the API is not contractually a string here); only a
    non-empty string code is a usable label, otherwise the caller's
    fallback (``http_<status>`` / ``unknown``) is more diagnostic than a
    literal ``"None"`` / ``""``.
    """
    error = line.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code else fallback


def _decode_lines(payload: Any) -> Iterator[dict[str, Any]]:
    """Yield parsed JSON objects from a JSONL file payload.

    The SDK's ``files.content`` returns a binary-response wrapper; read
    its text (``.text``, else ``.read()``). Blank lines are skipped;
    each non-blank line must be a JSON object.
    """
    text = getattr(payload, "text", None)
    if text is None:
        raw = payload.read()
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    for line in text.splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


class OpenAIBatchProcessor:
    """Submit, poll, and collect an OpenAI batch.

    Construct with an injected client (a fake in tests) or via
    ``from_env`` (lazy SDK import). Sync, and it does not poll on a
    timer: the caller owns the wait loop so a harness can interleave
    budget / cancellation checks (the same contract as the Anthropic
    wrapper).
    """

    def __init__(self, client: OpenAIBatchClient) -> None:
        self._client = client

    @classmethod
    def from_env(cls, *, api_key: str | None = None) -> OpenAIBatchProcessor:
        """Build from ``openai.OpenAI()`` (reads OPENAI_API_KEY).

        Lazily imports the optional ``openai`` SDK; raises a clear error
        naming the extra if it is absent (ADR 0007 idiom).
        """
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - exercised via env
            raise ImportError(
                "OpenAIBatchProcessor.from_env requires the 'openai' extra: "
                "pip install 'agents[openai]'"
            ) from exc
        client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()
        # The SDK client is a structural superset of the slice we use;
        # cast at the optional-SDK boundary (ADR 0007 idiom).
        return cls(cast("OpenAIBatchClient", client))

    def submit(self, requests: Sequence[OpenAIBatchRequest]) -> str:
        """Upload the JSONL input and create a batch; return its id."""
        if not requests:
            raise ValueError("submit requires at least one OpenAIBatchRequest")
        payload = "\n".join(r.to_jsonl_line() for r in requests).encode("utf-8")
        upload = self._client.files.create(
            file=("batch.jsonl", io.BytesIO(payload)), purpose="batch"
        )
        batch = self._client.batches.create(
            input_file_id=str(upload.id),
            endpoint=_CHAT_COMPLETIONS_ENDPOINT,
            completion_window="24h",
        )
        return str(batch.id)

    def status(self, batch_id: str) -> OpenAIBatchStatus:
        """Fetch current progress for a batch."""
        batch = self._client.batches.retrieve(batch_id)
        counts = getattr(batch, "request_counts", None)
        return OpenAIBatchStatus(
            id=str(batch.id),
            status=str(batch.status),
            completed=int(getattr(counts, "completed", 0) or 0),
            failed=int(getattr(counts, "failed", 0) or 0),
            total=int(getattr(counts, "total", 0) or 0),
        )

    def results(self, batch_id: str) -> Iterator[OpenAIBatchResult]:
        """Yield decoded results. Call only once ``status().ended``.

        A partially-failed batch splits its rows across two files:
        successes in ``output_file_id`` and failures in
        ``error_file_id``. Both are decoded (output first, then error)
        so the caller always sees every request; returning after the
        output file would silently drop the failed rows and make a
        partial failure look fully successful.
        """
        batch = self._client.batches.retrieve(batch_id)
        output_file_id = getattr(batch, "output_file_id", None)
        error_file_id = getattr(batch, "error_file_id", None)

        if output_file_id:
            content = self._client.files.content(str(output_file_id))
            for line in _decode_lines(content):
                custom_id = str(line.get("custom_id", ""))
                response = line.get("response") or {}
                status_code = response.get("status_code")
                if status_code == 200:
                    yield OpenAIBatchResult(
                        custom_id=custom_id,
                        type="succeeded",
                        text=_text_of(response.get("body")),
                    )
                else:
                    # A request-level failure can land in the output
                    # file with ``response: null`` and a structured
                    # ``error`` (distinct from the error-file rows). Use
                    # that error code when present so the diagnostic is
                    # not lost as a bare ``http_None``.
                    yield OpenAIBatchResult(
                        custom_id=custom_id,
                        type="errored",
                        error_type=_error_code(line, f"http_{status_code}"),
                    )

        if error_file_id:
            content = self._client.files.content(str(error_file_id))
            for line in _decode_lines(content):
                yield OpenAIBatchResult(
                    custom_id=str(line.get("custom_id", "")),
                    type="errored",
                    error_type=_error_code(line, "unknown"),
                )

    def cancel(self, batch_id: str) -> str:
        """Request cancellation; return the resulting status."""
        batch = self._client.batches.cancel(batch_id)
        return str(batch.status)
