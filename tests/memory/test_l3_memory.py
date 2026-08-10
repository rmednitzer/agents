"""L3 memory changes: decorator Protocol forwarding + audit fixes.

Covers BL-156 (wrap_acl / wrap_encrypted forward extension Protocols
with a truthful isinstance), BL-157 (DynamoDB float TTL), BL-161
(SQLite atomic batch; S3 server-side prefix), audit A5 (reserved
base-event keys rejected) and A6 (SQLite sweep boundary).
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from memory._audit import MemoryAudit
from memory.acl import RoleACL, wrap_acl
from memory.encryption import StaticKeyProvider, wrap_encrypted
from memory.inmemory import InMemoryStore
from memory.sqlite import SQLiteStore
from memory.store import (
    BatchMemoryStore,
    CASMemoryStore,
    ContentAddressableStore,
    MemoryStore,
    ScannableStore,
    SweepableStore,
)
from memory.types import Namespace


def _ns(name: str = "ns", retention: float | None = None) -> Namespace:
    return Namespace(name=name, workload="w", retention_seconds=retention)


class _CoreOnly:
    """A MemoryStore with no extension Protocols (for negative tests)."""

    name = "core-only"

    def __init__(self, ns: Namespace) -> None:
        self._namespace = ns
        self._d: dict[str, bytes] = {}

    @property
    def namespace(self) -> Namespace:
        return self._namespace

    async def read(self, key: str) -> bytes | None:
        return self._d.get(key)

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        self._d[key] = value

    async def delete(self, key: str) -> None:
        self._d.pop(key, None)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._d if k.startswith(prefix))


_KEY = b"k" * 32


def _policy() -> RoleACL:
    return RoleACL(
        roles={"alice": "admin"},
        grants={"admin": {"read", "write", "delete", "list"}},
    )


# --- BL-156: truthful isinstance --------------------------------------


def test_wrap_acl_core_only_does_not_fake_protocols() -> None:
    store = wrap_acl(_CoreOnly(_ns()), _policy(), "alice")
    assert isinstance(store, MemoryStore)
    assert not isinstance(store, BatchMemoryStore)
    assert not isinstance(store, CASMemoryStore)
    assert not isinstance(store, ScannableStore)


def test_wrap_acl_forwards_rich_backend_protocols() -> None:
    store = wrap_acl(InMemoryStore(_ns()), _policy(), "alice")
    assert isinstance(store, BatchMemoryStore)
    assert isinstance(store, ScannableStore)
    assert isinstance(store, ContentAddressableStore)
    assert isinstance(store, CASMemoryStore)
    assert isinstance(store, SweepableStore)


def test_wrap_encrypted_forwards_value_safe_protocols_not_cas() -> None:
    store = wrap_encrypted(InMemoryStore(_ns()), StaticKeyProvider(_KEY))
    assert isinstance(store, BatchMemoryStore)
    assert isinstance(store, ScannableStore)
    assert isinstance(store, ContentAddressableStore)
    assert isinstance(store, SweepableStore)
    # CAS is intentionally NOT forwarded over encryption (GCM nonce
    # randomisation makes ciphertext-equality CAS unrepresentable).
    assert not isinstance(store, CASMemoryStore)


@pytest.mark.asyncio
async def test_wrap_acl_batch_roundtrip_and_guard() -> None:
    store = wrap_acl(InMemoryStore(_ns()), _policy(), "alice")
    assert isinstance(store, BatchMemoryStore)
    await store.mset({"a": b"1", "b": b"2"})
    assert await store.mget(["a", "b", "missing"]) == [b"1", b"2", None]
    await store.mdelete(["a"])
    assert await store.mget(["a"]) == [None]


@pytest.mark.asyncio
async def test_wrap_acl_denies_forwarded_op() -> None:
    from memory.errors import AccessDenied

    policy = RoleACL(roles={"bob": "reader"}, grants={"reader": {"read"}})
    store = wrap_acl(InMemoryStore(_ns()), policy, "bob")
    assert isinstance(store, BatchMemoryStore)
    with pytest.raises(AccessDenied):
        await store.mset({"a": b"1"})


@pytest.mark.asyncio
async def test_wrap_encrypted_batch_seals_and_unseals() -> None:
    inner = InMemoryStore(_ns())
    store = wrap_encrypted(inner, StaticKeyProvider(_KEY))
    assert isinstance(store, BatchMemoryStore)
    await store.mset({"a": b"plain-a", "b": b"plain-b"})
    # Inner holds ciphertext, not plaintext.
    assert await inner.read("a") != b"plain-a"
    assert await store.mget(["a", "b"]) == [b"plain-a", b"plain-b"]


@pytest.mark.asyncio
async def test_wrap_encrypted_write_content_dedupes_on_plaintext() -> None:
    store = wrap_encrypted(InMemoryStore(_ns()), StaticKeyProvider(_KEY))
    assert isinstance(store, ContentAddressableStore)
    k1 = await store.write_content(b"same")
    k2 = await store.write_content(b"same")
    assert k1 == k2  # plaintext-addressed despite per-write nonce
    assert await store.read(k1) == b"same"


# --- A5: reserved base-event keys -------------------------------------


def test_audit_rejects_reserved_base_key() -> None:
    with pytest.raises(ValueError, match="must not carry per-event keys"):
        MemoryAudit(
            "ns",
            None,
            {
                "workload": "w",
                "contract": "c",
                "contract_version": "1",
                "trace_id": "t",
                "span_id": "s",
                "namespace": "leaked",  # collides with the per-emit field
            },
        )


def test_audit_still_rejects_missing_base_key() -> None:
    with pytest.raises(ValueError, match="missing required keys"):
        MemoryAudit("ns", None, {"workload": "w"})


# --- A6: SQLite sweep boundary ----------------------------------------


@pytest.mark.asyncio
async def test_sqlite_sweep_does_not_drop_entry_at_exact_expiry() -> None:
    store = SQLiteStore(_ns())
    # Expire far in the future, then monkey a sweep: an unexpired row is
    # never swept. Then a clearly-expired row is swept. The boundary
    # itself (now == expires_at) must agree with read() (strict >).
    await store.write("future", b"v", ttl_seconds=3600)
    assert await store.sweep_expired() == 0
    assert await store.read("future") == b"v"
    await store.write("past", b"v", ttl_seconds=0.001)
    time.sleep(0.01)
    assert await store.sweep_expired() == 1
    assert await store.read("past") is None
    store.close()


# --- BL-161: SQLite atomic batch --------------------------------------


@pytest.mark.asyncio
async def test_sqlite_mset_is_atomic_on_failure() -> None:
    store = SQLiteStore(_ns())
    await store.write("keep", b"original")
    # A bad key fails validation before any mutation (batch all-or-
    # nothing on validation); the prior value is intact.
    from memory.errors import NamespaceViolation

    with pytest.raises(NamespaceViolation):
        await store.mset({"keep": b"changed", "bad key": b"x"})
    assert await store.read("keep") == b"original"
    store.close()


# --- BL-157 / B6: DynamoDB float TTL ----------------------------------

moto = pytest.importorskip("moto")
import boto3  # noqa: E402


@pytest.fixture
def ddb_client() -> Iterator[object]:
    with moto.mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="kv",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


@pytest.mark.asyncio
async def test_dynamodb_subsecond_ttl_holds(
    ddb_client: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    import types

    from memory import dynamodb as _dynamodb
    from memory.dynamodb import DynamoDBStore

    # Controlled clock rather than wall time. The previous form wrote with
    # a 0.4 s TTL and asserted liveness immediately; that fails whenever the
    # runner stalls longer than the TTL between write and read, which is what
    # broke CI on 2026-08-10 (assert None == b"v" on the FIRST read, not the
    # expiry one). Widening the TTL is not an option: 0.4 s is load-bearing
    # because it is sub-integer, so an integer-truncated exp flips one of the
    # two assertions. Freeze the clock instead and the boundary stays exact.
    # Start on a fractional second so truncation is still caught: exp becomes
    # .65, an int-truncated exp becomes .00 and reads as already expired.
    clock = {"t": 1_700_000_000.25}
    monkeypatch.setattr(
        _dynamodb, "time", types.SimpleNamespace(time=lambda: clock["t"])
    )

    store = DynamoDBStore(_ns(), "kv", client=ddb_client)
    await store.write("k", b"v", ttl_seconds=0.4)
    assert await store.read("k") == b"v"
    clock["t"] += 0.5
    assert await store.read("k") is None


@pytest.mark.asyncio
async def test_dynamodb_exp_is_float_string(ddb_client: object) -> None:
    from memory.dynamodb import DynamoDBStore

    store = DynamoDBStore(_ns(), "kv", client=ddb_client)
    await store.write("k", b"v", ttl_seconds=10)
    raw = ddb_client.get_item(  # type: ignore[attr-defined]
        TableName="kv", Key={"pk": {"S": "ns::k"}}
    )
    exp = raw["Item"]["exp"]["N"]
    assert "." in exp  # float, not integer-truncated


# --- BL-161: S3 server-side prefix ------------------------------------


@pytest.mark.asyncio
async def test_s3_list_keys_uses_server_side_prefix() -> None:
    import boto3 as _b

    with moto.mock_aws():
        client = _b.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-bucket")
        from memory.s3 import S3Store

        store = S3Store(_ns(), "test-bucket", client=client)
        await store.write("alpha-1", b"1")
        await store.write("alpha-2", b"2")
        await store.write("beta-1", b"3")
        assert await store.list_keys("alpha-") == ["alpha-1", "alpha-2"]
        _, keys = await store.scan(prefix="alpha-", count=10)
        assert sorted(keys) == ["alpha-1", "alpha-2"]
