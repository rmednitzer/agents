"""Tests for harness.tools.ToolCatalog (BL-012)."""

from __future__ import annotations

from harness.mcp import ToolSpec
from harness.tools import ToolCatalog


def test_from_names_membership() -> None:
    cat = ToolCatalog.from_names(["search", "write"])
    assert "search" in cat
    assert "write" in cat
    assert "delete" not in cat
    assert 123 not in cat  # non-str is never a member


def test_from_specs_keys_on_name() -> None:
    cat = ToolCatalog.from_specs(
        [
            ToolSpec(name="a", description="x"),
            ToolSpec(name="b", description="y"),
        ]
    )
    assert set(cat.names()) == {"a", "b"}
    assert len(cat) == 2


def test_merge_unions() -> None:
    merged = ToolCatalog.from_names(["a"]).merge(ToolCatalog.from_names(["b", "a"]))
    assert set(merged) == {"a", "b"}


def test_iter_is_sorted() -> None:
    assert list(ToolCatalog.from_names(["c", "a", "b"])) == ["a", "b", "c"]


def test_equality_and_hash_are_value_based() -> None:
    a = ToolCatalog.from_names(["x", "y"])
    b = ToolCatalog.from_names(["y", "x"])
    assert a == b
    assert hash(a) == hash(b)
    assert a != ToolCatalog.from_names(["x"])
    assert a != "not a catalog"


def test_empty_catalog() -> None:
    cat = ToolCatalog()
    assert len(cat) == 0
    assert list(cat) == []
