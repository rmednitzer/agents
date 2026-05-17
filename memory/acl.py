"""Per-key / role-based access control for MemoryStore (BL-071, ADR 0007).

ADR 0004: "The harness's contract layer covers authorization at the
workload boundary; per-key ACLs are L2." ACLStore is that refinement: a
MemoryStore decorator bound to a single principal at construction
(isolation is structural, like namespace binding) that consults an
AccessPolicy before every operation and raises AccessDenied when the
principal's role does not permit it.

``Operation`` is one of read / write / delete / list. RoleACL is a
simple, declarative policy: role -> allowed operations, with optional
per-role key-prefix scoping. Richer policies (attribute-based, external
PDP) are just other AccessPolicy implementations.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Literal, Protocol, runtime_checkable

from memory.errors import AccessDenied
from memory.store import (
    BatchMemoryStore,
    CASMemoryStore,
    ContentAddressableStore,
    MemoryStore,
    ScannableStore,
    SweepableStore,
)
from memory.types import Namespace
from memory.validators import validate_key

__all__ = ["ACLStore", "AccessPolicy", "Operation", "RoleACL", "wrap_acl"]

Operation = Literal["read", "write", "delete", "list"]


@runtime_checkable
class AccessPolicy(Protocol):
    """Decides whether ``principal`` may perform ``operation`` on ``key``.

    For ``list``, ``key`` is the prefix being listed (possibly empty).
    Must be side-effect free.
    """

    def allows(self, principal: str, operation: Operation, key: str) -> bool: ...


class RoleACL:
    """Declarative role policy.

    ``roles`` maps a principal to its role name. ``grants`` maps a role
    to the operations it may perform. ``prefixes`` optionally scopes a
    role to keys under one of the given prefixes (empty/absent = all
    keys). An unknown principal or role is denied everything.
    """

    def __init__(
        self,
        roles: Mapping[str, str],
        grants: Mapping[str, set[Operation]],
        prefixes: Mapping[str, list[str]] | None = None,
    ) -> None:
        self._roles = dict(roles)
        self._grants = {r: set(ops) for r, ops in grants.items()}
        self._prefixes = {r: list(p) for r, p in (prefixes or {}).items()}

    def allows(self, principal: str, operation: Operation, key: str) -> bool:
        role = self._roles.get(principal)
        if role is None or operation not in self._grants.get(role, set()):
            return False
        scope = self._prefixes.get(role)
        if not scope:
            return True
        # The target (key, or the list prefix) must be WITHIN a granted
        # scope. A broad list prefix like "" or "team" is NOT authorized
        # for a role scoped to "team-a." -- otherwise ACLStore.list_keys
        # would return out-of-scope keys (data leak). Callers page their
        # subtree by passing a prefix at or below their scope.
        return any(key.startswith(p) for p in scope)


class ACLStore:
    """Wraps a MemoryStore, enforcing an AccessPolicy for one principal.

    The wrapped store remains the source of truth for namespace, key
    validation, TTL, audit, and value bytes; this decorator only gates
    access. It satisfies the MemoryStore Protocol and composes with
    EncryptedStore (wrap encryption innermost, ACL outermost).
    """

    name: str = "acl"

    def __init__(self, inner: MemoryStore, policy: AccessPolicy, principal: str) -> None:
        self._inner = inner
        self._policy = policy
        self._principal = principal

    @property
    def namespace(self) -> Namespace:
        return self._inner.namespace

    def _guard(self, operation: Operation, key: str) -> None:
        if not self._policy.allows(self._principal, operation, key):
            raise AccessDenied(self._principal, operation, key)

    async def read(self, key: str) -> bytes | None:
        # Key validation precedes the policy check: the MemoryStore
        # Protocol mandates validation before any keyed operation, and
        # the key-format rules are public, so ordering leaks nothing.
        validate_key(key)
        self._guard("read", key)
        return await self._inner.read(key)

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        validate_key(key)
        self._guard("write", key)
        await self._inner.write(key, value, ttl_seconds=ttl_seconds)

    async def delete(self, key: str) -> None:
        validate_key(key)
        self._guard("delete", key)
        await self._inner.delete(key)

    async def list_keys(self, prefix: str = "") -> list[str]:
        self._guard("list", prefix)
        return await self._inner.list_keys(prefix)


# --- Extension-Protocol forwarding (BL-156) ---------------------------
#
# A bare ACLStore implements only the core MemoryStore surface, so
# wrapping a CAS/batch/scan/content-addressing/sweepable backend hid
# those capabilities. Forwarding them unconditionally would be worse:
# isinstance(store, CASMemoryStore) would be True even over a backend
# that has no CAS, faking a capability the "don't fake it" contract
# (ADR 0004) forbids. So the methods live in mixins and ``wrap_acl``
# composes a class with exactly the mixins the wrapped store supports,
# preserving a truthful isinstance. The guard is applied to every
# forwarded operation, same as the core methods.


class _ACLBatchMixin:
    # _guard is ACLStore._guard, bound at composition; declared as an
    # attribute so the mixin type-checks standalone.
    _inner: BatchMemoryStore
    _guard: Callable[[Operation, str], None]

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        for k in keys:
            validate_key(k)
            self._guard("read", k)
        return await self._inner.mget(keys)

    async def mset(self, items: Mapping[str, bytes], *, ttl_seconds: float | None = None) -> None:
        for k in items:
            validate_key(k)
            self._guard("write", k)
        await self._inner.mset(items, ttl_seconds=ttl_seconds)

    async def mdelete(self, keys: Sequence[str]) -> None:
        for k in keys:
            validate_key(k)
            self._guard("delete", k)
        await self._inner.mdelete(keys)


class _ACLScanMixin:
    _inner: ScannableStore
    _guard: Callable[[Operation, str], None]

    async def scan(
        self, *, cursor: str = "", prefix: str = "", count: int = 100
    ) -> tuple[str, list[str]]:
        self._guard("list", prefix)
        return await self._inner.scan(cursor=cursor, prefix=prefix, count=count)


class _ACLContentMixin:
    _inner: ContentAddressableStore
    _guard: Callable[[Operation, str], None]

    async def write_content(self, value: bytes, *, ttl_seconds: float | None = None) -> str:
        import hashlib

        # The content key is public (sha256 of the value); the write
        # must still be authorized for that key.
        self._guard("write", hashlib.sha256(value).hexdigest())
        return await self._inner.write_content(value, ttl_seconds=ttl_seconds)


class _ACLCASMixin:
    _inner: CASMemoryStore
    _guard: Callable[[Operation, str], None]

    async def compare_and_set(
        self,
        key: str,
        expected: bytes | None,
        new: bytes,
        *,
        ttl_seconds: float | None = None,
    ) -> bool:
        validate_key(key)
        self._guard("write", key)
        return await self._inner.compare_and_set(key, expected, new, ttl_seconds=ttl_seconds)

    async def compare_and_delete(self, key: str, expected: bytes) -> bool:
        validate_key(key)
        self._guard("delete", key)
        return await self._inner.compare_and_delete(key, expected)


class _ACLSweepMixin:
    _inner: SweepableStore

    async def sweep_expired(self) -> int:
        # Sweep is a store-maintenance operation, not a per-key access;
        # it removes only already-expired entries. No ACL check (there
        # is no principal-scoped key), matching list's coarse gate.
        return await self._inner.sweep_expired()


def wrap_acl(inner: MemoryStore, policy: AccessPolicy, principal: str) -> ACLStore:
    """ACLStore that also forwards whatever extension Protocols ``inner``
    supports (BL-156).

    Returns a plain ``ACLStore`` when ``inner`` is core-only, or an
    ``ACLStore`` subclass mixing in batch / scan / content-addressing /
    CAS / sweep forwarding for exactly the Protocols ``inner`` satisfies,
    so ``isinstance`` stays truthful. Use this instead of constructing
    ``ACLStore`` directly when the backend is capability-rich.
    """
    mixins: list[type] = []
    if isinstance(inner, BatchMemoryStore):
        mixins.append(_ACLBatchMixin)
    if isinstance(inner, ScannableStore):
        mixins.append(_ACLScanMixin)
    if isinstance(inner, ContentAddressableStore):
        mixins.append(_ACLContentMixin)
    if isinstance(inner, CASMemoryStore):
        mixins.append(_ACLCASMixin)
    if isinstance(inner, SweepableStore):
        mixins.append(_ACLSweepMixin)
    if not mixins:
        return ACLStore(inner, policy, principal)
    cls = type("ACLStore", (ACLStore, *mixins), {})
    return cls(inner, policy, principal)  # type: ignore[no-any-return]
