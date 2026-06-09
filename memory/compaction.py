"""Memory compaction and summarisation driver (BL-234, BL-135 close).

Long-horizon workloads accumulate many small entries (turn logs, tool
results, observations). Lazy expiry (BL-195), the active sweeper
(BL-080), and the size-bound capacity pass (BL-212) reclaim space by
*dropping* entries; compaction reclaims space by *condensing* them: N
source entries are folded into one summary entry and the sources are
deleted.

Design, mirroring the BL-131 semantic layer:

- ``Summarizer`` is memory's own minimal structural Protocol, so the
  framework binds no vendor (ADR 0001). A model-quality (LLM-backed)
  summarizer satisfies it out of tree; the workload injects it.
- ``TruncatingSummarizer`` is the deterministic, dependency-free
  reference: a byte-budget head-plus-tail truncation. It is a
  size-reduction baseline, not a semantic summarizer, exactly as
  ``skills.HashingEmbeddingProvider`` (BL-110) is a similarity
  baseline, not a semantic embedder.
- ``MemoryCompactor`` is a driver class over existing store Protocols
  (the ``TTLSweeper`` precedent, BL-080/BL-212), not a new store
  Protocol: no adapter changes, nothing to fake (ADR 0004).

Atomicity: with ``atomic=True`` (the default) the store must implement
both ``VersionedMemoryStore`` and ``TransactionalMemoryStore`` (checked
at construction, so the configuration error surfaces at load time, not
mid-run, ADR 0007). The compactor reads every source with its version
token and commits the summary write plus all source deletes in one
version-gated transaction: a source rewritten, expired, or deleted
between read and commit fails the whole transaction and ``compact``
returns ``None`` (no lost update, no partial application; the caller
re-reads and retries). On DynamoDB the transaction is one
``TransactWriteItems`` call, which caps at 100 items: at most 99 live
sources per compact call there (the summary write is the 100th item).

``atomic=False`` is the explicit opt-in for backends without multi-key
transactions (S3): plain reads, then write-summary-then-delete-sources.
A crash between the write and the deletes leaves summary and sources
both present (re-compacting is safe; data is never lost), but a
concurrent writer can update a source after it was read and lose that
update when the source is deleted. Use it only under the
single-writer-per-key posture the demotion paths (BL-224/BL-225)
document.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

from memory.store import (
    MemoryStore,
    TransactionalMemoryStore,
    TxnDelete,
    TxnWrite,
    VersionedMemoryStore,
)
from memory.validators import validate_key

__all__ = [
    "CompactionResult",
    "MemoryCompactor",
    "Summarizer",
    "TruncatingSummarizer",
]


@runtime_checkable
class Summarizer(Protocol):
    """Condenses an ordered list of values into one value.

    The contract is bytes-in, bytes-out (the store's own currency);
    workloads that summarise text decode/encode at their boundary. An
    LLM-backed implementation satisfies this out of tree; the in-tree
    reference is ``TruncatingSummarizer``. ``summarize([])`` must
    return ``b""``.
    """

    async def summarize(self, values: list[bytes]) -> bytes: ...


class TruncatingSummarizer:
    """Deterministic head-plus-tail byte-budget reference Summarizer.

    Joins the values with ``separator`` and, when the result exceeds
    ``max_bytes``, keeps the head and the tail around an elision
    ``marker`` so the output is exactly ``max_bytes`` long. The head
    gets the larger half of the budget (earliest context usually
    carries the task framing; the tail keeps the most recent entries).

    This is a size-reduction baseline, not a semantic summarizer: it
    guarantees the byte budget and determinism, nothing about meaning.
    Inject a model-backed ``Summarizer`` for semantic quality.
    """

    def __init__(
        self,
        max_bytes: int,
        *,
        separator: bytes = b"\n",
        marker: bytes = b" ... ",
    ) -> None:
        # Load-time validation (ADR 0007): the marker must leave at
        # least one byte of content budget, or every over-budget input
        # would degenerate to (a slice of) the marker alone.
        if max_bytes <= len(marker):
            raise ValueError(
                f"max_bytes must exceed the marker length ({len(marker)}); got {max_bytes}"
            )
        self._max_bytes = max_bytes
        self._separator = separator
        self._marker = marker

    async def summarize(self, values: list[bytes]) -> bytes:
        joined = self._separator.join(values)
        if len(joined) <= self._max_bytes:
            return joined
        budget = self._max_bytes - len(self._marker)
        head_len = budget - budget // 2
        tail_len = budget // 2
        # Guard the tail slice: ``joined[-0:]`` is the whole value, so
        # a one-byte budget (head 1, tail 0) must yield b"" here.
        tail = joined[-tail_len:] if tail_len > 0 else b""
        return joined[:head_len] + self._marker + tail


@dataclass(frozen=True)
class CompactionResult:
    """Outcome of one successful ``MemoryCompactor.compact`` call.

    ``source_keys`` are the live keys whose values fed the summary, in
    deduplicated input order (the rolling target included when it was
    live among ``keys``). ``bytes_before`` sums those source values;
    ``bytes_after`` is the summary length. ``version`` is the summary
    entry's new content-hash token in atomic mode, ``None`` in
    best-effort mode (plain ``write`` returns no token).
    """

    target_key: str
    source_keys: tuple[str, ...]
    bytes_before: int
    bytes_after: int
    version: str | None


class MemoryCompactor:
    """Folds many source entries into one summary entry.

    A driver over store Protocols (the ``TTLSweeper`` precedent), bound
    to one store and one ``Summarizer`` at construction. See the module
    docstring for the atomic-versus-best-effort contract.

    Usage::

        compactor = MemoryCompactor(store, TruncatingSummarizer(4096))
        result = await compactor.compact(
            ["turn-001", "turn-002", "turn-003"],
            target_key="summary",
        )
        if result is None:
            ...  # nothing live, or a concurrent writer won; re-read and retry

    Rolling compaction: ``target_key`` may itself appear in ``keys``,
    so the previous summary is folded together with the new entries
    into the same target. The target is then version-gated like every
    source but excluded from the deletes.
    """

    def __init__(
        self,
        store: MemoryStore,
        summarizer: Summarizer,
        *,
        atomic: bool = True,
    ) -> None:
        if atomic:
            # Surface the configuration error at load time, not mid-run
            # (ADR 0007; the TTLSweeper max_keys precedent). Both
            # Protocols are required: the read side needs version
            # tokens, the commit side needs the multi-key transaction.
            if not isinstance(store, VersionedMemoryStore):
                raise TypeError(
                    "atomic compaction requires a VersionedMemoryStore; "
                    f"{type(store).__name__} does not implement read_versioned"
                )
            if not isinstance(store, TransactionalMemoryStore):
                raise TypeError(
                    "atomic compaction requires a TransactionalMemoryStore; "
                    f"{type(store).__name__} does not implement transact "
                    "(pass atomic=False to accept best-effort semantics)"
                )
        self._store = store
        self._summarizer = summarizer
        self._atomic = atomic

    async def compact(
        self,
        keys: Sequence[str],
        *,
        target_key: str,
        ttl_seconds: float | None = None,
    ) -> CompactionResult | None:
        """Summarise the live values of ``keys`` into ``target_key``.

        Validates every key up front (all-or-nothing, the
        BatchMemoryStore convention) and deduplicates ``keys``
        preserving first occurrence. Absent and expired sources are
        skipped. Returns ``None`` when no source is live, or (atomic
        mode) when a concurrent writer invalidated a version token
        between read and commit; the store is unchanged in both cases
        and the caller may re-read and retry. ``ttl_seconds`` applies
        to the summary entry (``None`` falls back to the namespace
        default, like ``write``).
        """
        validate_key(target_key)
        ordered = list(dict.fromkeys(keys))
        if not ordered:
            raise ValueError("keys must be non-empty")
        for key in ordered:
            validate_key(key)
        if self._atomic:
            return await self._compact_atomic(ordered, target_key, ttl_seconds)
        return await self._compact_best_effort(ordered, target_key, ttl_seconds)

    async def _compact_atomic(
        self,
        ordered: list[str],
        target_key: str,
        ttl_seconds: float | None,
    ) -> CompactionResult | None:
        # The __init__ isinstance checks guarantee both surfaces exist;
        # the casts keep mypy happy without re-narrowing per call (the
        # TTLSweeper capacity-pass idiom).
        versioned = cast(VersionedMemoryStore, self._store)
        transactional = cast(TransactionalMemoryStore, self._store)
        live: list[tuple[str, bytes, str]] = []
        target_version: str | None = None
        for key in ordered:
            hit = await versioned.read_versioned(key)
            if hit is None:
                continue
            value, token = hit
            live.append((key, value, token))
            if key == target_key:
                target_version = token
        if target_key not in ordered:
            target_hit = await versioned.read_versioned(target_key)
            target_version = None if target_hit is None else target_hit[1]
        if not live:
            return None
        summary = await self._summarizer.summarize([value for _, value, _ in live])
        committed = await transactional.transact(
            writes={
                target_key: TxnWrite(
                    summary,
                    expected_version=target_version,
                    ttl_seconds=ttl_seconds,
                )
            },
            deletes={
                key: TxnDelete(expected_version=token)
                for key, _, token in live
                if key != target_key
            },
        )
        if committed is None:
            return None
        return CompactionResult(
            target_key=target_key,
            source_keys=tuple(key for key, _, _ in live),
            bytes_before=sum(len(value) for _, value, _ in live),
            bytes_after=len(summary),
            version=committed[target_key],
        )

    async def _compact_best_effort(
        self,
        ordered: list[str],
        target_key: str,
        ttl_seconds: float | None,
    ) -> CompactionResult | None:
        live: list[tuple[str, bytes]] = []
        for key in ordered:
            value = await self._store.read(key)
            if value is None:
                continue
            live.append((key, value))
        if not live:
            return None
        summary = await self._summarizer.summarize([value for _, value in live])
        # Write the summary before deleting sources: a crash in between
        # leaves both present (safe to re-compact), never neither.
        await self._store.write(target_key, summary, ttl_seconds=ttl_seconds)
        for key, _ in live:
            if key != target_key:
                await self._store.delete(key)
        return CompactionResult(
            target_key=target_key,
            source_keys=tuple(key for key, _ in live),
            bytes_before=sum(len(value) for _, value in live),
            bytes_after=len(summary),
            version=None,
        )
