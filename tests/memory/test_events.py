"""Memory operation events through EventSink (BL-040)."""

from __future__ import annotations

import asyncio

import pytest

from harness.events import MemoryDelete, MemoryRead, MemoryWrite
from harness.sinks import MemorySink
from memory.inmemory import InMemoryStore
from memory.types import Namespace


def _ns() -> Namespace:
    return Namespace(name="audit-ns", workload="w")


def _base() -> dict[str, str]:
    return {
        "workload": "w",
        "contract": "c",
        "contract_version": "1.0",
        "trace_id": "t1",
        "span_id": "s1",
    }


@pytest.mark.asyncio
async def test_no_events_without_base_fields() -> None:
    """A store with a sink but no base fields stays silent (standalone use)."""
    sink = MemorySink()
    store = InMemoryStore(_ns(), sink=sink)
    await store.write("k", b"v")
    await store.read("k")
    await store.delete("k")
    assert sink.events == []


@pytest.mark.asyncio
async def test_write_read_delete_emit_events() -> None:
    sink = MemorySink()
    store = InMemoryStore(_ns(), sink=sink, base_event_fields=_base())

    await store.write("k", b"hello", ttl_seconds=30.0)
    await store.read("k")
    await store.read("missing")
    await store.delete("k")
    await store.delete("k")  # idempotent; existed=False

    kinds = [type(e) for e in sink.events]
    assert kinds == [MemoryWrite, MemoryRead, MemoryRead, MemoryDelete, MemoryDelete]

    w = sink.events[0]
    assert isinstance(w, MemoryWrite)
    assert w.namespace == "audit-ns"
    assert w.key == "k"
    assert w.value_bytes == 5
    assert w.ttl_seconds == 30.0
    assert w.trace_id == "t1"

    hit, miss = sink.events[1], sink.events[2]
    assert isinstance(hit, MemoryRead)
    assert hit.hit is True
    assert isinstance(miss, MemoryRead)
    assert miss.hit is False

    existed, absent = sink.events[3], sink.events[4]
    assert isinstance(existed, MemoryDelete)
    assert existed.existed is True
    assert isinstance(absent, MemoryDelete)
    assert absent.existed is False


def test_partial_base_fields_rejected_at_construction() -> None:
    """A partial base dict is a load-time config error, not a mid-run one."""
    with pytest.raises(ValueError, match="missing required keys"):
        InMemoryStore(_ns(), base_event_fields={"workload": "w"})


@pytest.mark.asyncio
async def test_delete_of_expired_key_audits_existed_false() -> None:
    """An expired-but-unswept key is absent; the delete audit must agree."""
    sink = MemorySink()
    store = InMemoryStore(_ns(), sink=sink, base_event_fields=_base())
    await store.write("k", b"v", ttl_seconds=0.02)
    await store.write("m", b"v", ttl_seconds=0.02)
    await asyncio.sleep(0.05)
    sink.events.clear()
    await store.delete("k")
    await store.mdelete(["m"])
    deletes = [e for e in sink.events if isinstance(e, MemoryDelete)]
    assert [e.existed for e in deletes] == [False, False]


@pytest.mark.asyncio
async def test_events_are_serializable_for_audit_packs() -> None:
    sink = MemorySink()
    store = InMemoryStore(_ns(), sink=sink, base_event_fields=_base())
    await store.write("k", b"v")
    payload = sink.events[0].model_dump_json()
    assert '"kind":"memory_write"' in payload
