"""Exception hierarchy for the memory subsystem."""

from __future__ import annotations

__all__ = [
    "AccessDenied",
    "MemoryError",
    "NamespaceViolation",
]


class MemoryError(Exception):
    """Base for memory subsystem errors.

    Distinct from builtins.MemoryError, which is raised by the runtime
    on allocation failure. This class is for memory-store policy and
    integrity violations.
    """


class NamespaceViolation(MemoryError):
    """Operation violates namespace isolation or key format rules.

    Raised on:
    - Empty key.
    - Key longer than 256 characters.
    - Key containing the internal separator '::'.
    - Key containing path traversal characters ('..', '/', '\\\\').
    - Key containing whitespace or null bytes.
    - Invalid namespace name format.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"Namespace violation: {reason}")
        self.reason = reason


class AccessDenied(MemoryError):
    """A principal is not authorized for an operation on a key (BL-071).

    Raised by ACLStore when the bound principal's role does not permit
    the attempted operation (read/write/delete/list) on the given key.
    """

    def __init__(self, principal: str, operation: str, key: str) -> None:
        super().__init__(f"principal {principal!r} denied {operation!r} on key {key!r}")
        self.principal = principal
        self.operation = operation
        self.key = key
