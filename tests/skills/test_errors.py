"""Tests for skills.errors."""

from __future__ import annotations

from skills.errors import NoSkillFound, SkillLoadError, SkillManifestError


def test_skill_load_error_contains_path_and_reason() -> None:
    err = SkillLoadError("/tmp/skill", "missing SKILL.md")
    assert err.path == "/tmp/skill"
    assert err.reason == "missing SKILL.md"
    assert "Failed to load skill at /tmp/skill" in str(err)


def test_skill_manifest_error_contains_path_and_reason() -> None:
    err = SkillManifestError("/tmp/skill/SKILL.md", "yaml parse failure")
    assert err.path == "/tmp/skill/SKILL.md"
    assert err.reason == "yaml parse failure"
    assert "is invalid" in str(err)


def test_no_skill_found_contains_query_and_dispatcher() -> None:
    err = NoSkillFound("question", "keyword")
    assert err.query == "question"
    assert err.dispatcher == "keyword"
    assert "found no skill for query" in str(err)
