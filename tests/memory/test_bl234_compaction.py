"""BL-234 (BL-135 compaction half): Summarizer + MemoryCompactor.

Compaction folds N source entries into one summary entry and deletes
the sources. Tests cover:

- ``TruncatingSummarizer``: passthrough at or under budget, exact
  ``max_bytes`` output when over, head/tail preservation around the
  marker, the zero-tail slice guard, separator handling, determinism,
  empty input, and load-time marker/budget validation;
- ``MemoryCompactor`` construction: ``atomic=True`` fails fast on a
  store missing either ``VersionedMemoryStore`` or
  ``TransactionalMemoryStore`` (load-time TypeError, ADR 0007);
  ``atomic=False`` accepts a core-only store;
- atomic compaction: sources merged in input order and deleted in the
  same transaction, the result carries the new content-hash token,
  rolling compaction (target among the sources), dedupe, absent and
  expired sources skipped, all-absent returning None, version-token
  conflicts (source rewritten, target created concurrently) returning
  None with no partial application, TTL passthrough to the summary;
- best-effort compaction on a core-only store: write-then-delete with
  ``version=None``;
- audit: the transactional commit emits one MemoryWrite for the target
  and one MemoryDelete per source (BL-040 via the store's own ops).
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from harness.events import MemoryDelete, MemoryWrite
from harness.sinks import MemorySink
from memory.compaction import (
    CompactionResult,
    MemoryCompactor,
    Summarizer,
    TruncatingSummarizer,
)
from memory.errors import NamespaceViolation
from memory.inmemory import InMemoryStore
from memory.types import Namespace


def _store(**kwargs: object) -> InMemoryStore:
    return InMemoryStore(Namespace(name="compact", workload="w"), **kwargs)  # type: ignore[arg-type]


class _CoreOnlyStore:
    """Minimal L1-only MemoryStore double (no versioning, no transactions)."""

    name = "core-only"

    def __init__(self) -> None:
        self.namespace = Namespace(name="compact", workload="w")
        self._data: dict[str, bytes] = {}

    async def read(self, key: str) -> bytes | None:
        return self._data.get(key)

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        self._data[key] = value

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._data if k.startswith(prefix))


class _VersionedOnlyStore(_CoreOnlyStore):
    """Versioned but not transactional (isinstance checks are split)."""

    async def read_versioned(self, key: str) -> tuple[bytes, str] | None:
        value = self._data.get(key)
        if value is None:
            return None
        return value, hashlib.sha256(value).hexdigest()

    async def write_versioned(
        self,
        key: str,
        value: bytes,
        *,
        expected_version: str | None = None,
        ttl_seconds: float | None = None,
    ) -> str | None:
        return None

    async def delete_versioned(self, key: str, expected_version: str) -> bool:
        return False


class _RacingSummarizer:
    """Summarizer that writes ``key`` mid-summarise to force a conflict.

    Models a concurrent writer landing between the compactor's
    versioned reads and its transactional commit; the await inside
    ``compact`` is the suspension point a real interleaving would use.
    """

    def __init__(self, store: InMemoryStore, key: str, value: bytes) -> None:
        self._store = store
        self._key = key
        self._value = value

    async def summarize(self, values: list[bytes]) -> bytes:
        await self._store.write(self._key, self._value)
        return b"|".join(values)


# ---- TruncatingSummarizer ----------------------------------------------


@pytest.mark.asyncio
async def test_truncating_passthrough_under_budget() -> None:
    s = TruncatingSummarizer(64, separator=b"|")
    assert await s.summarize([b"one", b"two"]) == b"one|two"


@pytest.mark.asyncio
async def test_truncating_passthrough_at_exact_budget() -> None:
    s = TruncatingSummarizer(7, separator=b"|")
    assert await s.summarize([b"one", b"two"]) == b"one|two"


@pytest.mark.asyncio
async def test_truncating_over_budget_is_exactly_max_bytes() -> None:
    s = TruncatingSummarizer(16, separator=b"")
    out = await s.summarize([b"0123456789ABCDEFGHIJ"])
    assert len(out) == 16
    # Budget 11 around the 5-byte default marker: head gets the larger
    # half (6), tail the rest (5).
    assert out == b"012345" + b" ... " + b"FGHIJ"


@pytest.mark.asyncio
async def test_truncating_zero_tail_budget_guard() -> None:
    # budget 1 -> head 1, tail 0. A naive joined[-0:] would append the
    # whole value; the guard pins the output to exactly max_bytes.
    s = TruncatingSummarizer(2, marker=b"#")
    out = await s.summarize([b"abcdef"])
    assert out == b"a#"


@pytest.mark.asyncio
async def test_truncating_empty_values_yield_empty() -> None:
    s = TruncatingSummarizer(8)
    assert await s.summarize([]) == b""


@pytest.mark.asyncio
async def test_truncating_is_deterministic() -> None:
    s = TruncatingSummarizer(10, separator=b",")
    values = [b"alpha", b"beta", b"gamma"]
    assert await s.summarize(values) == await s.summarize(values)


@pytest.mark.parametrize("bad", [5, 4, 0, -1])
def test_truncating_rejects_budget_not_exceeding_marker(bad: int) -> None:
    # Default marker is 5 bytes; max_bytes must exceed it.
    with pytest.raises(ValueError, match="marker"):
        TruncatingSummarizer(bad)


def test_truncating_satisfies_summarizer_protocol() -> None:
    assert isinstance(TruncatingSummarizer(16), Summarizer)


# ---- MemoryCompactor construction ----------------------------------------


def test_atomic_rejects_store_without_versioning() -> None:
    with pytest.raises(TypeError, match="VersionedMemoryStore"):
        MemoryCompactor(_CoreOnlyStore(), TruncatingSummarizer(16))


def test_atomic_rejects_store_without_transactions() -> None:
    with pytest.raises(TypeError, match="TransactionalMemoryStore"):
        MemoryCompactor(_VersionedOnlyStore(), TruncatingSummarizer(16))


def test_best_effort_accepts_core_only_store() -> None:
    MemoryCompactor(_CoreOnlyStore(), TruncatingSummarizer(16), atomic=False)


# ---- atomic compaction -----------------------------------------------------


@pytest.mark.asyncio
async def test_compact_merges_sources_and_deletes_them() -> None:
    s = _store()
    await s.write("a", b"1111")
    await s.write("b", b"22")
    await s.write("c", b"3")
    c = MemoryCompactor(s, TruncatingSummarizer(1024, separator=b"|"))
    result = await c.compact(["a", "b", "c"], target_key="t")
    assert result == CompactionResult(
        target_key="t",
        source_keys=("a", "b", "c"),
        bytes_before=7,
        bytes_after=9,
        version=hashlib.sha256(b"1111|22|3").hexdigest(),
    )
    assert await s.read("t") == b"1111|22|3"
    assert sorted(await s.list_keys()) == ["t"]


@pytest.mark.asyncio
async def test_compact_result_version_matches_live_token() -> None:
    s = _store()
    await s.write("a", b"x")
    c = MemoryCompactor(s, TruncatingSummarizer(1024))
    result = await c.compact(["a"], target_key="t")
    assert result is not None
    hit = await s.read_versioned("t")
    assert hit is not None
    assert hit[1] == result.version


@pytest.mark.asyncio
async def test_rolling_compaction_folds_target_into_itself() -> None:
    s = _store()
    await s.write("summary", b"old")
    await s.write("n1", b"new1")
    await s.write("n2", b"new2")
    c = MemoryCompactor(s, TruncatingSummarizer(1024, separator=b"|"))
    result = await c.compact(["summary", "n1", "n2"], target_key="summary")
    assert result is not None
    assert result.source_keys == ("summary", "n1", "n2")
    assert await s.read("summary") == b"old|new1|new2"
    # The target was version-gated but not deleted; the new entries are.
    assert sorted(await s.list_keys()) == ["summary"]


@pytest.mark.asyncio
async def test_compact_skips_absent_and_expired_sources() -> None:
    s = _store()
    await s.write("dead", b"x", ttl_seconds=0.02)
    await s.write("live", b"y")
    await asyncio.sleep(0.05)
    c = MemoryCompactor(s, TruncatingSummarizer(1024, separator=b"|"))
    result = await c.compact(["dead", "missing", "live"], target_key="t")
    assert result is not None
    assert result.source_keys == ("live",)
    assert await s.read("t") == b"y"


@pytest.mark.asyncio
async def test_compact_with_no_live_sources_returns_none() -> None:
    s = _store()
    c = MemoryCompactor(s, TruncatingSummarizer(1024))
    assert await c.compact(["missing1", "missing2"], target_key="t") is None
    assert await s.read("t") is None


@pytest.mark.asyncio
async def test_compact_dedupes_keys_preserving_order() -> None:
    s = _store()
    await s.write("a", b"1")
    await s.write("b", b"2")
    c = MemoryCompactor(s, TruncatingSummarizer(1024, separator=b"|"))
    result = await c.compact(["a", "a", "b", "a"], target_key="t")
    assert result is not None
    assert result.source_keys == ("a", "b")
    assert await s.read("t") == b"1|2"


@pytest.mark.asyncio
async def test_compact_empty_keys_raises() -> None:
    c = MemoryCompactor(_store(), TruncatingSummarizer(1024))
    with pytest.raises(ValueError, match="non-empty"):
        await c.compact([], target_key="t")


@pytest.mark.asyncio
async def test_compact_validates_keys_before_any_read() -> None:
    s = _store()
    await s.write("ok", b"1")
    c = MemoryCompactor(s, TruncatingSummarizer(1024))
    with pytest.raises(NamespaceViolation):
        await c.compact(["ok", "bad key"], target_key="t")
    with pytest.raises(NamespaceViolation):
        await c.compact(["ok"], target_key="bad/target")
    # No partial application from either failed call.
    assert sorted(await s.list_keys()) == ["ok"]


@pytest.mark.asyncio
async def test_concurrent_source_rewrite_aborts_compaction() -> None:
    s = _store()
    await s.write("a", b"1")
    await s.write("b", b"2")
    c = MemoryCompactor(s, _RacingSummarizer(s, "a", b"changed"))
    assert await c.compact(["a", "b"], target_key="t") is None
    # The racing write won; nothing else moved (no lost update, no
    # partial application).
    assert await s.read("a") == b"changed"
    assert await s.read("b") == b"2"
    assert await s.read("t") is None


@pytest.mark.asyncio
async def test_concurrent_target_creation_aborts_compaction() -> None:
    s = _store()
    await s.write("a", b"1")
    c = MemoryCompactor(s, _RacingSummarizer(s, "t", b"sniped"))
    assert await c.compact(["a"], target_key="t") is None
    assert await s.read("t") == b"sniped"  # the concurrent write is preserved
    assert await s.read("a") == b"1"  # the source was not deleted


@pytest.mark.asyncio
async def test_compact_ttl_applies_to_summary() -> None:
    s = _store()
    await s.write("a", b"1")
    c = MemoryCompactor(s, TruncatingSummarizer(1024))
    result = await c.compact(["a"], target_key="t", ttl_seconds=0.02)
    assert result is not None
    assert await s.read("t") == b"1"
    await asyncio.sleep(0.05)
    assert await s.read("t") is None


# ---- best-effort compaction ------------------------------------------------


@pytest.mark.asyncio
async def test_best_effort_compacts_core_only_store() -> None:
    s = _CoreOnlyStore()
    await s.write("a", b"1")
    await s.write("b", b"2")
    c = MemoryCompactor(s, TruncatingSummarizer(1024, separator=b"|"), atomic=False)
    result = await c.compact(["a", "b"], target_key="t")
    assert result == CompactionResult(
        target_key="t",
        source_keys=("a", "b"),
        bytes_before=2,
        bytes_after=3,
        version=None,
    )
    assert await s.read("t") == b"1|2"
    assert await s.read("a") is None
    assert await s.read("b") is None


@pytest.mark.asyncio
async def test_best_effort_rolling_keeps_target() -> None:
    s = _CoreOnlyStore()
    await s.write("t", b"old")
    await s.write("a", b"new")
    c = MemoryCompactor(s, TruncatingSummarizer(1024, separator=b"|"), atomic=False)
    result = await c.compact(["t", "a"], target_key="t")
    assert result is not None
    assert await s.read("t") == b"old|new"
    assert await s.list_keys() == ["t"]


@pytest.mark.asyncio
async def test_best_effort_no_live_sources_returns_none() -> None:
    s = _CoreOnlyStore()
    c = MemoryCompactor(s, TruncatingSummarizer(1024), atomic=False)
    assert await c.compact(["missing"], target_key="t") is None
    assert await s.read("t") is None


# ---- audit (BL-040 via the store's own operations) --------------------------


@pytest.mark.asyncio
async def test_compact_emits_write_and_delete_audit_events() -> None:
    sink = MemorySink()
    s = InMemoryStore(
        Namespace(name="compact", workload="w"),
        sink=sink,
        base_event_fields={
            "workload": "w",
            "contract": "c",
            "contract_version": "1",
            "trace_id": "t",
            "span_id": "s",
        },
    )
    for key in ("a", "b", "c"):
        await s.write(key, b"v")
    c = MemoryCompactor(s, TruncatingSummarizer(1024))
    result = await c.compact(["a", "b", "c"], target_key="summary")
    assert result is not None
    writes = [e for e in sink.events if isinstance(e, MemoryWrite)]
    deletes = [e for e in sink.events if isinstance(e, MemoryDelete)]
    # 3 setup writes + 1 transactional summary write; 3 source deletes.
    assert len(writes) == 4
    assert len(deletes) == 3
