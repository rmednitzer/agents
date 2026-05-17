"""Optional Anthropic API capabilities for workloads (BL-186, ADR 0012).

Two capabilities a workload can profit from that the single-run
``Runtime`` Protocol does not cover:

- **Message Batches.** ``AnthropicBatchProcessor`` wraps the Anthropic
  Message Batches API (``POST /v1/messages/batches``): asynchronous bulk
  processing at 50% of standard token price, up to 100k requests per
  batch. The right shape for non-latency-sensitive fan-out (offline
  evaluation sets, corpus reclassification, backfills) where the
  per-call ``Runtime`` adapter would be needlessly slow and expensive.
- **Prompt caching.** ``cache_control_system`` builds a correctly shaped
  cached system block. Caching is a prefix match, so the cached content
  must be the stable prefix; the helper documents and enforces that
  shape.

Both follow the repo's optional-backend convention (ADR 0007): the
``anthropic`` SDK is an optional extra, imported lazily, and this module
imports and type-checks with the SDK absent. ``AnthropicBatchProcessor``
takes the batches resource by dependency injection, so its logic is
fully testable with a fake; only ``from_env`` touches the real SDK.

Default model is ``claude-opus-4-7`` per the current Anthropic guidance;
override per request when a cheaper model fits (batch classification is
a common ``claude-haiku-4-5`` case).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DEFAULT_MODEL",
    "AnthropicBatchProcessor",
    "BatchRequest",
    "BatchResult",
    "BatchStatus",
    "BatchesResource",
    "cache_control_system",
]

DEFAULT_MODEL = "claude-opus-4-7"
"""Default model for batch requests (current most-capable Claude)."""

_CacheTTL = Literal["5m", "1h"]


def cache_control_system(text: str, *, ttl: _CacheTTL = "5m") -> list[dict[str, Any]]:
    """Return a one-block system list with a prompt-cache breakpoint.

    Prompt caching is a prefix match: any byte change before the
    breakpoint invalidates the cache. Pass only the *stable* system
    prefix here (the frozen instructions / shared context). Put volatile
    content (timestamps, per-request ids, the actual question) in
    ``messages`` after this block, never interpolated into ``text``
    (interpolating a timestamp here silently makes every request a
    cache miss).

    ``ttl`` is the cache lifetime: ``"5m"`` (default, 1.25x write cost)
    or ``"1h"`` (2x write cost, survives bursty gaps). Verify hits via
    ``response.usage.cache_read_input_tokens`` (zero across identical
    prefixes means a silent invalidator).
    """
    cache_control: dict[str, Any] = {"type": "ephemeral"}
    if ttl == "1h":
        cache_control["ttl"] = "1h"
    return [{"type": "text", "text": text, "cache_control": cache_control}]


class BatchRequest(BaseModel):
    """One request in a batch. ``custom_id`` correlates the result."""

    model_config = ConfigDict(frozen=True)

    custom_id: str
    messages: list[dict[str, Any]]
    model: str = DEFAULT_MODEL
    max_tokens: int = Field(default=16000, gt=0)
    system: str | list[dict[str, Any]] | None = None
    thinking: dict[str, Any] | None = None

    def to_wire(self) -> dict[str, Any]:
        """Render the ``{custom_id, params}`` shape the API expects.

        ``params`` is a non-streaming Messages request. Optional fields
        are omitted when unset so the request body stays minimal and
        cache-stable.
        """
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self.messages,
        }
        if self.system is not None:
            params["system"] = self.system
        if self.thinking is not None:
            params["thinking"] = self.thinking
        return {"custom_id": self.custom_id, "params": params}


class BatchStatus(BaseModel):
    """Snapshot of a batch's progress."""

    model_config = ConfigDict(frozen=True)

    id: str
    processing_status: str
    succeeded: int = 0
    errored: int = 0
    processing: int = 0

    @property
    def ended(self) -> bool:
        """True once the batch has finished (no more results pending)."""
        return self.processing_status == "ended"


class BatchResult(BaseModel):
    """One decoded result from a completed batch.

    ``type`` is the API result type (``succeeded`` / ``errored`` /
    ``canceled`` / ``expired``). ``text`` is the concatenation of the
    message's text blocks on success, else None. ``error_type`` is the
    API error type on failure (``invalid_request`` is a permanent
    client error; others are retryable).
    """

    model_config = ConfigDict(frozen=True)

    custom_id: str
    type: str
    text: str | None = None
    error_type: str | None = None


class BatchesResource(Protocol):
    """The slice of ``client.messages.batches`` this module uses.

    Injected so the processor is testable without the SDK. The real
    object is ``anthropic.Anthropic().messages.batches``.
    """

    def create(self, *, requests: list[dict[str, Any]]) -> Any: ...

    def retrieve(self, batch_id: str, /) -> Any: ...

    def results(self, batch_id: str, /) -> Iterator[Any]: ...

    def cancel(self, batch_id: str, /) -> Any: ...


def _text_of(message: Any) -> str | None:
    """Concatenate the text blocks of a returned message, or None."""
    content = getattr(message, "content", None)
    if content is None:
        return None
    parts = [
        block.text
        for block in content
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ]
    return "".join(parts) if parts else None


class AnthropicBatchProcessor:
    """Submit, poll, and collect Anthropic Message Batches.

    Construct with the batches resource directly (inject a fake in
    tests) or via ``from_env`` (lazy SDK import). This wrapper is sync
    and does not poll on a timer: the caller owns the wait loop so a
    harness can interleave budget / cancellation checks.
    """

    def __init__(self, batches: BatchesResource) -> None:
        self._batches = batches

    @classmethod
    def from_env(cls, *, api_key: str | None = None) -> AnthropicBatchProcessor:
        """Build from ``anthropic.Anthropic()`` (reads ANTHROPIC_API_KEY).

        Lazily imports the optional ``anthropic`` SDK; raises a clear
        error naming the extra if it is not installed (ADR 0007 idiom).
        """
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised via env
            raise ImportError(
                "AnthropicBatchProcessor.from_env requires the 'anthropic' "
                "extra: pip install 'agents[anthropic]'"
            ) from exc
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        # The SDK's batches resource is a structural superset of the
        # slice we use (wider create kwargs); cast at the optional-SDK
        # boundary, the ADR 0007 idiom for wrapping a backend behind a
        # typed Protocol.
        return cls(cast("BatchesResource", client.messages.batches))

    def submit(self, requests: Sequence[BatchRequest]) -> str:
        """Create a batch; return its id. Empty input is rejected."""
        if not requests:
            raise ValueError("submit requires at least one BatchRequest")
        batch = self._batches.create(requests=[r.to_wire() for r in requests])
        return str(batch.id)

    def status(self, batch_id: str) -> BatchStatus:
        """Fetch current progress for a batch."""
        batch = self._batches.retrieve(batch_id)
        counts = getattr(batch, "request_counts", None)
        return BatchStatus(
            id=str(batch.id),
            processing_status=str(batch.processing_status),
            succeeded=int(getattr(counts, "succeeded", 0) or 0),
            errored=int(getattr(counts, "errored", 0) or 0),
            processing=int(getattr(counts, "processing", 0) or 0),
        )

    def results(self, batch_id: str) -> Iterator[BatchResult]:
        """Yield decoded results. Call only once ``status().ended``.

        Each entry's ``result.type`` discriminates success from the
        terminal failure modes; only ``succeeded`` carries a message.
        """
        for entry in self._batches.results(batch_id):
            result = entry.result
            kind = str(result.type)
            if kind == "succeeded":
                yield BatchResult(
                    custom_id=str(entry.custom_id),
                    type=kind,
                    text=_text_of(result.message),
                )
            elif kind == "errored":
                yield BatchResult(
                    custom_id=str(entry.custom_id),
                    type=kind,
                    error_type=str(getattr(result.error, "type", "unknown")),
                )
            else:
                yield BatchResult(custom_id=str(entry.custom_id), type=kind)

    def cancel(self, batch_id: str) -> str:
        """Request cancellation; return the resulting processing status."""
        batch = self._batches.cancel(batch_id)
        return str(batch.processing_status)
