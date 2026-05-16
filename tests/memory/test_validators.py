"""Tests for memory.validators."""

from __future__ import annotations

import pytest

from memory.errors import NamespaceViolation
from memory.validators import (
    KEY_MAX_LENGTH,
    validate_key,
    validate_namespace_name,
)


def test_valid_keys_accepted() -> None:
    for k in ("simple", "with-hyphen", "with_underscore", "with.dot", "abc:1", "a"):
        validate_key(k)


def test_empty_key_rejected() -> None:
    with pytest.raises(NamespaceViolation, match="empty"):
        validate_key("")


def test_too_long_key_rejected() -> None:
    with pytest.raises(NamespaceViolation, match="too long"):
        validate_key("a" * (KEY_MAX_LENGTH + 1))


def test_max_length_key_accepted() -> None:
    validate_key("a" * KEY_MAX_LENGTH)


def test_double_colon_rejected() -> None:
    with pytest.raises(NamespaceViolation, match="'::'"):
        validate_key("ns::key")


def test_path_traversal_rejected() -> None:
    with pytest.raises(NamespaceViolation):
        validate_key("../escape")


def test_forward_slash_rejected() -> None:
    with pytest.raises(NamespaceViolation):
        validate_key("nested/key")


def test_backslash_rejected() -> None:
    with pytest.raises(NamespaceViolation):
        validate_key("nested\\key")


def test_null_byte_rejected() -> None:
    with pytest.raises(NamespaceViolation):
        validate_key("with\0null")


def test_whitespace_rejected() -> None:
    with pytest.raises(NamespaceViolation, match="whitespace"):
        validate_key("with space")


def test_tab_rejected() -> None:
    with pytest.raises(NamespaceViolation, match="whitespace"):
        validate_key("with\ttab")


def test_namespace_name_valid() -> None:
    for n in ("a", "ns1", "ns-1", "ns_1", "a" * 64):
        validate_namespace_name(n)


def test_namespace_name_empty_rejected() -> None:
    with pytest.raises(NamespaceViolation):
        validate_namespace_name("")


def test_namespace_name_too_long_rejected() -> None:
    with pytest.raises(NamespaceViolation):
        validate_namespace_name("a" * 65)


def test_namespace_name_uppercase_rejected() -> None:
    with pytest.raises(NamespaceViolation):
        validate_namespace_name("Foo")


def test_namespace_name_starts_with_hyphen_rejected() -> None:
    with pytest.raises(NamespaceViolation):
        validate_namespace_name("-foo")
