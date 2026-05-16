"""Tests for skills.types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from skills.types import SkillManifest, SkillMatch


def test_minimal_manifest() -> None:
    m = SkillManifest(name="my-skill", description="What it does. Use when.")
    assert m.name == "my-skill"
    assert m.description == "What it does. Use when."
    assert m.license is None
    assert m.compatibility is None
    assert m.metadata == {}
    assert m.allowed_tools is None


def test_full_manifest() -> None:
    m = SkillManifest(
        name="full-skill",
        description="Full description.",
        license="Apache-2.0",
        compatibility="Requires git",
        metadata={"lane": "ops", "triggers": "deploy,rollback"},
    )
    assert m.metadata["lane"] == "ops"


def test_allowed_tools_alias_hyphen() -> None:
    raw = {
        "name": "x",
        "description": "d",
        "allowed-tools": "Bash(git:*) Read",
    }
    m = SkillManifest.model_validate(raw)
    assert m.allowed_tools == "Bash(git:*) Read"


def test_allowed_tools_python_name_also_accepted() -> None:
    """Both 'allowed-tools' (alias) and 'allowed_tools' (field) are accepted."""
    m = SkillManifest(name="x", description="d", allowed_tools="Read")
    assert m.allowed_tools == "Read"


def test_manifest_is_frozen() -> None:
    m = SkillManifest(name="x", description="d")
    with pytest.raises(ValidationError):
        m.name = "y"  # type: ignore[misc]


def test_name_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillManifest(name="a" * 65, description="d")


def test_name_empty_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillManifest(name="", description="d")


def test_name_uppercase_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillManifest(name="MySkill", description="d")


def test_name_leading_hyphen_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillManifest(name="-skill", description="d")


def test_name_trailing_hyphen_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillManifest(name="skill-", description="d")


def test_name_consecutive_hyphens_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillManifest(name="my--skill", description="d")


def test_name_underscore_accepted_via_metadata() -> None:
    """Per spec, name allows lowercase alphanumeric and hyphens only.

    Underscore is explicitly out per the spec (only hyphens for separation).
    """
    with pytest.raises(ValidationError):
        SkillManifest(name="my_skill", description="d")


def test_description_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillManifest(name="x", description="a" * 1025)


def test_description_empty_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillManifest(name="x", description="")


def test_compatibility_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillManifest(name="x", description="d", compatibility="a" * 501)


def test_skill_match_confidence_range() -> None:
    m = SkillMatch(skill_name="x", confidence=0.5, rationale="r", dispatcher="d")
    assert m.confidence == 0.5


def test_skill_match_confidence_above_1_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillMatch(skill_name="x", confidence=1.5, rationale="r", dispatcher="d")


def test_skill_match_negative_confidence_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillMatch(skill_name="x", confidence=-0.1, rationale="r", dispatcher="d")
