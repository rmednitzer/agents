"""Exception hierarchy for the memory subsystem."""

from __future__ import annotations

__all__ = [
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
