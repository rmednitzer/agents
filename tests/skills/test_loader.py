"""Tests for skills.loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from skills.errors import SkillLoadError, SkillManifestError
from skills.loader import discover_skill, parse_skill_md


def _write_skill(tmp: Path, name: str, body: str = "Body.") -> Path:
    skill_dir = tmp / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A test skill.\n---\n{body}",
        encoding="utf-8",
    )
    return skill_dir


def test_parse_minimal_skill_md(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "minimal", "Just a body.")
    manifest, body = parse_skill_md(skill_dir / "SKILL.md")
    assert manifest.name == "minimal"
    assert manifest.description == "A test skill."
    assert body.strip() == "Just a body."


def test_parse_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SkillLoadError, match="not found"):
        parse_skill_md(tmp_path / "nope.md")


def test_parse_missing_frontmatter_raises(tmp_path: Path) -> None:
    p = tmp_path / "SKILL.md"
    p.write_text("No frontmatter here.\n", encoding="utf-8")
    with pytest.raises(SkillManifestError, match="frontmatter"):
        parse_skill_md(p)


def test_parse_unclosed_frontmatter_raises(tmp_path: Path) -> None:
    p = tmp_path / "SKILL.md"
    p.write_text("---\nname: x\ndescription: d\n", encoding="utf-8")
    with pytest.raises(SkillManifestError, match="closing"):
        parse_skill_md(p)


def test_parse_invalid_yaml_raises(tmp_path: Path) -> None:
    p = tmp_path / "SKILL.md"
    p.write_text(
        "---\nname: x\ndescription: d\n  bad_indent: [unclosed\n---\nbody",
        encoding="utf-8",
    )
    with pytest.raises(SkillManifestError):
        parse_skill_md(p)


def test_parse_validation_failure_raises(tmp_path: Path) -> None:
    p = tmp_path / "SKILL.md"
    p.write_text(
        "---\nname: BadName\ndescription: d\n---\nbody",
        encoding="utf-8",
    )
    with pytest.raises(SkillManifestError):
        parse_skill_md(p)


def test_discover_skill_returns_skill(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "my-skill")
    skill = discover_skill(skill_dir)
    assert skill.name == "my-skill"
    assert skill.path == skill_dir
    assert skill.references == {}
    assert skill.scripts == {}
    assert skill.assets == {}


def test_discover_skill_finds_references(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "with-refs")
    refs_dir = skill_dir / "references"
    refs_dir.mkdir()
    (refs_dir / "REFERENCE.md").write_text("detail")
    (refs_dir / "FORMS.md").write_text("forms")
    skill = discover_skill(skill_dir)
    assert "REFERENCE.md" in skill.references
    assert "FORMS.md" in skill.references


def test_discover_skill_finds_scripts(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "with-scripts")
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print('hi')")
    skill = discover_skill(skill_dir)
    assert "run.py" in skill.scripts


def test_discover_skill_name_mismatch_raises(tmp_path: Path) -> None:
    # Directory is 'one' but manifest declares 'two'
    skill_dir = tmp_path / "one"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: two\ndescription: d\n---\nbody", encoding="utf-8"
    )
    with pytest.raises(SkillManifestError, match="directory name"):
        discover_skill(skill_dir)


def test_discover_skill_not_a_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "file"
    file_path.write_text("not a directory")
    with pytest.raises(SkillLoadError, match="not a directory"):
        discover_skill(file_path)


def test_skill_body_lazy_loading(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "lazy", "Lazy body content.")
    skill = discover_skill(skill_dir)
    # _body starts as None
    assert skill._body is None
    body = skill.body()
    assert "Lazy body content." in body
    # second call returns cached
    assert skill.body() is body
