"""Tests for BL-122: AttributeACL + audited AccessDenied event."""

from __future__ import annotations

import pytest

from harness.sinks import MemorySink
from memory.acl import ACLStore, AttributeACL, AttributeRule, RoleACL, wrap_acl
from memory.errors import AccessDenied
from memory.inmemory import InMemoryStore
from memory.types import Namespace

_BASE = {
    "workload": "w",
    "contract": "c",
    "contract_version": "1",
    "trace_id": "t",
    "span_id": "s",
}


def _inner(name: str = "ns") -> InMemoryStore:
    return InMemoryStore(Namespace(name=name, workload="w"))


# --- AttributeACL (ABAC) ---------------------------------------------


def _abac() -> AttributeACL:
    return AttributeACL(
        attributes={
            "svc-pay": {"team": "payments", "clearance": "high"},
            "svc-mkt": {"team": "marketing", "clearance": "low"},
        },
        rules=[
            # payments may read/write anything.
            AttributeRule(
                operations=frozenset({"read", "write", "delete", "list"}),
                match={"team": "payments"},
            ),
            # any high-clearance principal may read the audit/ prefix.
            AttributeRule(
                operations=frozenset({"read"}),
                match={"clearance": "high"},
                prefixes=("audit.",),
            ),
        ],
    )


@pytest.mark.asyncio
async def test_attribute_acl_grants_by_attribute() -> None:
    s = ACLStore(_inner(), _abac(), "svc-pay")
    await s.write("k", b"v")
    assert await s.read("k") == b"v"
    await s.delete("k")


@pytest.mark.asyncio
async def test_attribute_acl_denies_unmatched_attribute() -> None:
    s = ACLStore(_inner(), _abac(), "svc-mkt")
    with pytest.raises(AccessDenied):
        await s.write("k", b"v")


@pytest.mark.asyncio
async def test_attribute_acl_prefix_scoped_rule() -> None:
    s = ACLStore(_inner(), _abac(), "svc-mkt")
    # marketing is low clearance: the audit. read rule needs high.
    with pytest.raises(AccessDenied):
        await s.read("audit.log")


@pytest.mark.asyncio
async def test_attribute_acl_unknown_principal_denied() -> None:
    s = ACLStore(_inner(), _abac(), "ghost")
    with pytest.raises(AccessDenied):
        await s.read("anything")


def test_attribute_acl_is_side_effect_free() -> None:
    policy = _abac()
    assert policy.allows("svc-pay", "write", "k") is True
    assert policy.allows("svc-pay", "write", "k") is True
    assert policy.allows("svc-mkt", "write", "k") is False


def test_empty_match_rule_grants_every_principal() -> None:
    policy = AttributeACL(
        attributes={},
        rules=[AttributeRule(operations=frozenset({"read"}), prefixes=("pub.",))],
    )
    assert policy.allows("anyone", "read", "pub.x") is True
    assert policy.allows("anyone", "read", "private.x") is False
    assert policy.allows("anyone", "write", "pub.x") is False


# --- BL-122: audited AccessDenied event ------------------------------


@pytest.mark.asyncio
async def test_denial_emits_access_denied_event() -> None:
    sink = MemorySink()
    policy = RoleACL(roles={"bob": "reader"}, grants={"reader": {"read"}})
    s = ACLStore(_inner(), policy, "bob", sink=sink, base_event_fields=_BASE)
    with pytest.raises(AccessDenied):
        await s.write("k", b"v")
    denied = [e for e in sink.events if e.kind == "access_denied"]
    assert len(denied) == 1
    ev = denied[0]
    assert ev.principal == "bob"
    assert ev.operation == "write"
    assert ev.key == "k"
    assert ev.namespace == "ns"
    assert ev.workload == "w"


@pytest.mark.asyncio
async def test_no_base_fields_is_silent() -> None:
    sink = MemorySink()
    policy = RoleACL(roles={"bob": "reader"}, grants={"reader": {"read"}})
    s = ACLStore(_inner(), policy, "bob", sink=sink)  # no base_event_fields
    with pytest.raises(AccessDenied):
        await s.write("k", b"v")
    assert sink.events == []


@pytest.mark.asyncio
async def test_allowed_access_emits_no_denial() -> None:
    sink = MemorySink()
    policy = RoleACL(roles={"alice": "admin"}, grants={"admin": {"read", "write"}})
    s = ACLStore(_inner(), policy, "alice", sink=sink, base_event_fields=_BASE)
    await s.write("k", b"v")
    assert await s.read("k") == b"v"
    assert [e for e in sink.events if e.kind == "access_denied"] == []


@pytest.mark.asyncio
async def test_wrap_acl_forwards_audit_and_caps() -> None:
    sink = MemorySink()
    policy = RoleACL(roles={"bob": "reader"}, grants={"reader": {"read"}})
    inner = _inner()
    store = wrap_acl(inner, policy, "bob", sink=sink, base_event_fields=_BASE)
    from memory.store import BatchMemoryStore

    assert isinstance(store, BatchMemoryStore)  # extension forwarding intact
    with pytest.raises(AccessDenied):
        await store.mset({"k": b"v"})
    denied = [e for e in sink.events if e.kind == "access_denied"]
    assert denied
    assert denied[0].operation == "write"


def test_reserved_base_field_rejected() -> None:
    policy = RoleACL(roles={}, grants={})
    with pytest.raises(ValueError, match="principal"):
        ACLStore(
            _inner(),
            policy,
            "x",
            sink=MemorySink(),
            base_event_fields={**_BASE, "principal": "oops"},
        )
