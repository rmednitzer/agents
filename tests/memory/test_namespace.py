"""Tests for memory.types.Namespace."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from memory.errors import NamespaceViolation
from memory.types import Namespace


def test_namespace_valid_name() -> None:
    ns = Namespace(name="workload-state", workload="example")
    assert ns.name == "workload-state"
    assert ns.workload == "example"
    assert ns.retention_seconds is None


def test_namespace_with_retention() -> None:
    ns = Namespace(name="session", workload="example", retention_seconds=3600.0)
    assert ns.retention_seconds == 3600.0


def test_namespace_alphanumeric_with_underscore_and_hyphen() -> None:
    ns = Namespace(name="ns_1-test", workload="w")
    assert ns.name == "ns_1-test"


def test_namespace_rejects_uppercase() -> None:
    with pytest.raises(NamespaceViolation):
        Namespace(name="MyNamespace", workload="w")


def test_namespace_rejects_starting_with_hyphen() -> None:
    with pytest.raises(NamespaceViolation):
        Namespace(name="-foo", workload="w")


def test_namespace_rejects_starting_with_underscore() -> None:
    with pytest.raises(NamespaceViolation):
        Namespace(name="_foo", workload="w")


def test_namespace_rejects_empty_name() -> None:
    with pytest.raises(NamespaceViolation):
        Namespace(name="", workload="w")


def test_namespace_rejects_special_chars() -> None:
    with pytest.raises(NamespaceViolation):
        Namespace(name="ns/path", workload="w")


def test_namespace_rejects_too_long_name() -> None:
    with pytest.raises(NamespaceViolation):
        Namespace(name="a" * 65, workload="w")


def test_namespace_rejects_zero_retention() -> None:
    with pytest.raises(ValidationError):
        Namespace(name="x", workload="w", retention_seconds=0)


def test_namespace_rejects_negative_retention() -> None:
    with pytest.raises(ValidationError):
        Namespace(name="x", workload="w", retention_seconds=-1.0)


def test_namespace_is_frozen() -> None:
    ns = Namespace(name="x", workload="w")
    with pytest.raises(ValidationError):
        ns.name = "y"  # type: ignore[misc]
