"""Key and namespace name validation.

Centralizes the rules so all MemoryStore adapters enforce the same
keyspace contract. Raises NamespaceViolation on any rule failure.
"""

from __future__ import annotations

import re

from memory.errors import NamespaceViolation

__all__ = [
    "KEY_MAX_LENGTH",
    "NAMESPACE_NAME_PATTERN",
    "validate_key",
    "validate_namespace_name",
]

KEY_MAX_LENGTH = 256
NAMESPACE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_KEY_FORBIDDEN_SUBSTRINGS = ("::", "..", "/", "\\", "\0")


def validate_key(key: str) -> None:
    """Validate a memory key.

    Rules:
    - Must be non-empty.
    - Max length KEY_MAX_LENGTH (256) characters.
    - Must not contain '::' (the internal namespace separator).
    - Must not contain path traversal patterns ('..', '/', '\\\\').
    - Must not contain null bytes.
    - Must not contain whitespace.

    Raises NamespaceViolation on any rule failure.
    """
    if not key:
        raise NamespaceViolation("key cannot be empty")
    if len(key) > KEY_MAX_LENGTH:
        raise NamespaceViolation(f"key too long ({len(key)} > {KEY_MAX_LENGTH})")
    for bad in _KEY_FORBIDDEN_SUBSTRINGS:
        if bad in key:
            raise NamespaceViolation(f"key cannot contain {bad!r}")
    if any(c.isspace() for c in key):
        raise NamespaceViolation("key cannot contain whitespace")


def validate_namespace_name(name: str) -> None:
    """Validate a namespace name.

    Rules:
    - 1-64 characters.
    - First character lowercase alphanumeric.
    - Remaining characters lowercase alphanumeric, underscore, or hyphen.

    Raises NamespaceViolation on rule failure.
    """
    if not name:
        raise NamespaceViolation("namespace name cannot be empty")
    if not NAMESPACE_NAME_PATTERN.match(name):
        raise NamespaceViolation(
            f"namespace name {name!r} does not match ^[a-z0-9][a-z0-9_-]{{0,63}}$"
        )
