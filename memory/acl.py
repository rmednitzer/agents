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

from collections.abc import Mapping
from typing import Literal, Protocol, runtime_checkable

from memory.errors import AccessDenied
from memory.store import MemoryStore
from memory.types import Namespace

__all__ = ["ACLStore", "AccessPolicy", "Operation", "RoleACL"]

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
        # 'list' authorizes when its prefix is within (or contains) a
        # granted scope so a caller can page its own subtree.
        return any(key.startswith(p) or (operation == "list" and p.startswith(key)) for p in scope)


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
        self._guard("read", key)
        return await self._inner.read(key)

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        self._guard("write", key)
        await self._inner.write(key, value, ttl_seconds=ttl_seconds)

    async def delete(self, key: str) -> None:
        self._guard("delete", key)
        await self._inner.delete(key)

    async def list_keys(self, prefix: str = "") -> list[str]:
        self._guard("list", prefix)
        return await self._inner.list_keys(prefix)
