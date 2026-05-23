"""Tests for BL-180: TransactionalMemoryStore atomic multi-key transactions.

The same generic boundary tests run against every backend that
implements ``TransactionalMemoryStore``: ``InMemoryStore`` and
``SQLiteStore`` (the reference / single-host adapters), plus the durable
network adapters ``RedisStore`` (WATCH/MULTI/EXEC) and ``DynamoDBStore``
(TransactWriteItems). ``S3Store`` is intentionally excluded for the same
reason it does not implement CAS or VersionedMemoryStore: no native
atomic compare-and-set / multi-key atomicity (ADR 0004 "don't fake it").
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from memory.inmemory import InMemoryStore
from memory.sqlite import SQLiteStore
from memory.store import TransactionalMemoryStore, TxnDelete, TxnWrite
from memory.types import Namespace


def _ns(retention: float | None = None) -> Namespace:
    return Namespace(name="ns", workload="w", retention_seconds=retention)


@pytest.fixture(params=["inmemory", "sqlite", "redis", "dynamodb"])
def store(request: pytest.FixtureRequest) -> Iterator[TransactionalMemoryStore]:
    backend = request.param
    if backend == "inmemory":
        yield InMemoryStore(_ns())
        return
    if backend == "sqlite":
        s = SQLiteStore(_ns())
        yield s
        s.close()
        return
    if backend == "redis":
        fakeredis = pytest.importorskip("fakeredis")
        from memory.redis import RedisStore

        client = fakeredis.aioredis.FakeRedis()
        yield RedisStore(_ns(), client=client)
        return
    if backend == "dynamodb":
        moto = pytest.importorskip("moto")
        import boto3

        from memory.dynamodb import DynamoDBStore

        with moto.mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            client.create_table(
                TableName="kv",
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
            yield DynamoDBStore(_ns(), "kv", client=client, consistent_read=True)
        return
    raise AssertionError(f"unknown backend: {backend}")  # pragma: no cover


def test_satisfies_protocol(store: TransactionalMemoryStore) -> None:
    assert isinstance(store, TransactionalMemoryStore)


@pytest.mark.asyncio
async def test_empty_transaction_returns_empty_dict(
    store: TransactionalMemoryStore,
) -> None:
    """Trivial empty transactions are valid no-ops, returning an empty
    map. Distinct from a precondition failure (which returns None)."""
    assert await store.transact() == {}
    assert await store.transact(writes={}, deletes={}) == {}


@pytest.mark.asyncio
async def test_create_two_keys_atomically(store: TransactionalMemoryStore) -> None:
    out = await store.transact(
        writes={
            "a": TxnWrite(value=b"v-a"),
            "b": TxnWrite(value=b"v-b"),
        }
    )
    assert out is not None
    assert set(out) == {"a", "b"}
    # Tokens are content-hashes, so distinct values yield distinct tokens.
    assert out["a"] != out["b"]
    assert await store.read("a") == b"v-a"
    assert await store.read("b") == b"v-b"


@pytest.mark.asyncio
async def test_all_or_nothing_on_precondition_failure(
    store: TransactionalMemoryStore,
) -> None:
    """If any precondition fails, no key is written. The classic atomic
    transfer test: pre-write k1 only, then attempt a transaction that
    expects both k1 and k2 absent. k1's precondition fails, so the new
    write to k2 must also be absent after the transaction (no partial
    application)."""
    await store.write("k1", b"existing")
    out = await store.transact(
        writes={
            "k1": TxnWrite(value=b"new-k1"),  # expects absent, but exists
            "k2": TxnWrite(value=b"new-k2"),  # would succeed alone
        }
    )
    assert out is None
    assert await store.read("k1") == b"existing"  # unchanged
    assert await store.read("k2") is None  # NOT written


@pytest.mark.asyncio
async def test_optimistic_multi_key_update(
    store: TransactionalMemoryStore,
) -> None:
    """The reference use case: two keys' tokens are checked, both are
    updated atomically."""
    t_a = await store.write_versioned("a", b"v1")
    t_b = await store.write_versioned("b", b"v1")
    assert t_a is not None
    assert t_b is not None
    out = await store.transact(
        writes={
            "a": TxnWrite(value=b"v2", expected_version=t_a),
            "b": TxnWrite(value=b"v2", expected_version=t_b),
        }
    )
    assert out is not None
    assert out["a"] != t_a
    assert out["b"] != t_b
    assert await store.read("a") == b"v2"
    assert await store.read("b") == b"v2"


@pytest.mark.asyncio
async def test_stale_token_on_one_key_aborts(
    store: TransactionalMemoryStore,
) -> None:
    """If one of the expected tokens is stale, the whole transaction is
    a no-op even when the other token would have matched."""
    t_a_old = await store.write_versioned("a", b"v1")
    assert t_a_old is not None
    await store.write("a", b"changed")  # invalidates t_a_old
    t_b = await store.write_versioned("b", b"v1")
    assert t_b is not None
    out = await store.transact(
        writes={
            "a": TxnWrite(value=b"v2", expected_version=t_a_old),  # stale
            "b": TxnWrite(value=b"v2", expected_version=t_b),  # matches
        }
    )
    assert out is None
    assert await store.read("a") == b"changed"
    assert await store.read("b") == b"v1"  # NOT written


@pytest.mark.asyncio
async def test_write_and_delete_in_one_transaction(
    store: TransactionalMemoryStore,
) -> None:
    """A transaction combines writes and deletes; both classes of
    precondition are checked, the apply is atomic."""
    t_old = await store.write_versioned("delete-me", b"old")
    assert t_old is not None
    out = await store.transact(
        writes={"new-key": TxnWrite(value=b"v")},
        deletes={"delete-me": TxnDelete(expected_version=t_old)},
    )
    assert out is not None
    assert set(out) == {"new-key"}
    assert await store.read("new-key") == b"v"
    assert await store.read("delete-me") is None


@pytest.mark.asyncio
async def test_delete_with_wrong_token_aborts(
    store: TransactionalMemoryStore,
) -> None:
    await store.write("a", b"v")
    await store.write("b", b"v")
    out = await store.transact(
        writes={"a": TxnWrite(value=b"new", expected_version=None)},  # absent
        deletes={"b": TxnDelete(expected_version="0" * 64)},  # wrong token
    )
    assert out is None
    assert await store.read("a") == b"v"
    assert await store.read("b") == b"v"


@pytest.mark.asyncio
async def test_overlap_writes_and_deletes_is_rejected(
    store: TransactionalMemoryStore,
) -> None:
    """A key cannot appear in both ``writes`` and ``deletes``; the
    intersection is a caller bug rejected at the contract boundary."""
    with pytest.raises(ValueError, match="both writes and deletes"):
        await store.transact(
            writes={"k": TxnWrite(value=b"v")},
            deletes={"k": TxnDelete(expected_version="0" * 64)},
        )


@pytest.mark.asyncio
async def test_invalid_key_raises_at_boundary(
    store: TransactionalMemoryStore,
) -> None:
    """The MemoryStore Protocol mandates key validation before any
    keyed operation, including transactions."""
    from memory.errors import NamespaceViolation

    with pytest.raises(NamespaceViolation):
        await store.transact(writes={"bad::key": TxnWrite(value=b"v")})


@pytest.mark.asyncio
async def test_per_key_ttl_is_respected(
    store: TransactionalMemoryStore,
) -> None:
    """``TxnWrite.ttl_seconds`` is per-key; one key with a short TTL and
    another without should expire independently."""
    import asyncio

    out = await store.transact(
        writes={
            "ephemeral": TxnWrite(value=b"v", ttl_seconds=0.05),
            "persistent": TxnWrite(value=b"v"),
        }
    )
    assert out is not None
    await asyncio.sleep(0.12)
    assert await store.read("ephemeral") is None  # expired
    assert await store.read("persistent") == b"v"  # still live


@pytest.mark.asyncio
async def test_wrap_acl_forwards_transactional_protocol() -> None:
    """wrap_acl over a transactional backend keeps a truthful isinstance
    and gates each touched key (BL-156 contract)."""
    from memory.acl import RoleACL, wrap_acl
    from memory.errors import AccessDenied

    inner = InMemoryStore(_ns())
    policy = RoleACL(
        roles={"admin": "rw", "bob": "ro"},
        grants={"rw": {"read", "write", "delete"}, "ro": {"read"}},
    )
    admin = wrap_acl(inner, policy, "admin")
    assert isinstance(admin, TransactionalMemoryStore)
    out = await admin.transact(writes={"k": TxnWrite(value=b"v")})
    assert out is not None
    # The read-only principal cannot transact a write; AccessDenied
    # aborts the whole call (all-or-nothing).
    reader = wrap_acl(inner, policy, "bob")
    assert isinstance(reader, TransactionalMemoryStore)
    with pytest.raises(AccessDenied):
        await reader.transact(writes={"k": TxnWrite(value=b"nope")})
    # The inner state is unchanged (the guard runs before the inner call).
    assert (await inner.read("k")) == b"v"
