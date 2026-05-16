"""Tests for skills.validators (BL-012)."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.tools import ToolCatalog
from skills.errors import SkillManifestError
from skills.registry import SkillRegistry
from skills.types import Skill, SkillManifest
from skills.validators import (
    unknown_tools,
    validate_allowed_tools,
    validate_registry_tools,
)


def _skill(name: str, allowed: str | None) -> Skill:
    return Skill(
        manifest=SkillManifest(name=name, description="d", **{"allowed-tools": allowed}),
        path=Path("/tmp") / name,
    )


def test_allowed_tools_property_parses_space_separated() -> None:
    s = _skill("x", "search  write   read")
    assert s.allowed_tools == ["search", "write", "read"]


def test_allowed_tools_property_empty_when_unset() -> None:
    assert _skill("x", None).allowed_tools == []


def test_unknown_tools_reports_missing_sorted_deduped() -> None:
    s = _skill("x", "b a a c")
    cat = ToolCatalog.from_names(["a"])
    assert unknown_tools(s, cat) == ["b", "c"]


def test_validate_allowed_tools_passes_when_all_known() -> None:
    s = _skill("x", "search write")
    validate_allowed_tools(s, ToolCatalog.from_names(["search", "write", "read"]))


def test_validate_allowed_tools_raises_listing_offenders() -> None:
    s = _skill("x", "search danger")
    with pytest.raises(SkillManifestError, match="danger"):
        validate_allowed_tools(s, ToolCatalog.from_names(["search"]))


def test_validate_allowed_tools_noop_when_none_declared() -> None:
    validate_allowed_tools(_skill("x", None), ToolCatalog())


def test_validate_registry_tools_reports_only_offenders() -> None:
    reg = SkillRegistry()
    reg.add(_skill("good", "a b"))
    reg.add(_skill("bad", "a zzz"))
    reg.add(_skill("none", None))
    report = validate_registry_tools(reg, ToolCatalog.from_names(["a", "b"]))
    assert report == {"bad": ["zzz"]}
