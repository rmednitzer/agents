"""Tests for skills.registry."""

from __future__ import annotations

from pathlib import Path

from skills.registry import SkillRegistry
from skills.types import Skill, SkillManifest


def _make_skill(name: str, description: str = "d", lane: str | None = None) -> Skill:
    metadata = {"lane": lane} if lane else {}
    return Skill(
        manifest=SkillManifest(name=name, description=description, metadata=metadata),
        path=Path("/tmp/" + name),
    )


def test_empty_registry() -> None:
    r = SkillRegistry()
    assert len(r) == 0
    assert r.all() == []
    assert r.lanes() == []
    assert r.get("anything") is None


def test_add_and_get() -> None:
    r = SkillRegistry()
    s = _make_skill("x")
    r.add(s)
    assert len(r) == 1
    assert r.get("x") is s


def test_lane_indexing() -> None:
    r = SkillRegistry()
    r.add(_make_skill("a", lane="ops"))
    r.add(_make_skill("b", lane="ops"))
    r.add(_make_skill("c", lane="docs"))
    r.add(_make_skill("d"))  # no lane
    assert sorted(r.lanes()) == ["docs", "ops"]
    ops_skills = r.by_lane("ops")
    assert {s.name for s in ops_skills} == {"a", "b"}


def test_contains_operator() -> None:
    r = SkillRegistry()
    r.add(_make_skill("x"))
    assert "x" in r
    assert "y" not in r


def test_iteration() -> None:
    r = SkillRegistry()
    r.add(_make_skill("a"))
    r.add(_make_skill("b"))
    names = {s.name for s in r}
    assert names == {"a", "b"}


def test_add_duplicate_name_overwrites() -> None:
    r = SkillRegistry()
    r.add(_make_skill("x", description="first"))
    r.add(_make_skill("x", description="second"))
    assert r.get("x") is not None
    skill = r.get("x")
    assert skill is not None
    assert skill.description == "second"


def test_from_directory_loads_skills(tmp_path: Path) -> None:
    # Create three skill directories
    for name in ("alpha", "beta", "gamma"):
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A skill.\n---\nbody",
            encoding="utf-8",
        )
    r = SkillRegistry.from_directory(tmp_path)
    assert len(r) == 3
    assert {s.name for s in r.all()} == {"alpha", "beta", "gamma"}


def test_from_directory_skips_non_skill_directories(tmp_path: Path) -> None:
    # One valid skill, one directory without SKILL.md
    valid = tmp_path / "valid"
    valid.mkdir()
    (valid / "SKILL.md").write_text(
        "---\nname: valid\ndescription: d.\n---\nb",
        encoding="utf-8",
    )
    (tmp_path / "not-a-skill").mkdir()
    r = SkillRegistry.from_directory(tmp_path)
    assert len(r) == 1
    assert r.get("valid") is not None


def test_from_directory_missing_root_returns_empty(tmp_path: Path) -> None:
    r = SkillRegistry.from_directory(tmp_path / "does-not-exist")
    assert len(r) == 0


def _versioned(name: str, version: str) -> Skill:
    return Skill(
        manifest=SkillManifest(name=name, description="d", metadata={"version": version}),
        path=Path("/tmp/" + name),
    )


def test_bl053_versions_coexist_and_resolve_by_spec() -> None:
    r = SkillRegistry()
    r.add(_versioned("calc", "1.0.0"))
    r.add(_versioned("calc", "2.0.0"))
    assert sorted(r.versions("calc")) == ["1.0.0", "2.0.0"]
    v1 = r.get("calc@1.0.0")
    v2 = r.get("calc@2.0.0")
    assert v1 is not None
    assert v1.version == "1.0.0"
    assert v2 is not None
    assert v2.version == "2.0.0"
    assert r.get("calc@9.9.9") is None
    assert len(r) == 1  # one name, multiple versions


def test_bl053_current_is_most_recently_added() -> None:
    r = SkillRegistry()
    r.add(_versioned("calc", "1.0.0"))
    r.add(_versioned("calc", "2.0.0"))
    current = r.get("calc")
    assert current is not None
    assert current.version == "2.0.0"  # last added is current
    # Rollback by re-adding the older version makes it current again.
    r.add(_versioned("calc", "1.0.0"))
    rolled = r.get("calc")
    assert rolled is not None
    assert rolled.version == "1.0.0"


def test_bl053_unversioned_keeps_last_write_wins() -> None:
    r = SkillRegistry()
    r.add(_make_skill("x", description="first"))
    r.add(_make_skill("x", description="second"))
    skill = r.get("x")
    assert skill is not None
    assert skill.description == "second"
    assert r.versions("x") == ["0.0.0"]


def test_lane_reindexed_when_current_version_changes() -> None:
    """A new version with a different lane must not leave stale lane entries."""
    r = SkillRegistry()
    r.add(
        Skill(
            manifest=SkillManifest(
                name="svc", description="d", metadata={"version": "1.0.0", "lane": "ops"}
            ),
            path=Path("/tmp/svc"),
        )
    )
    assert r.by_lane("ops")
    assert r.by_lane("ops")[0].name == "svc"
    # Upgrade: same name, new version, different lane.
    r.add(
        Skill(
            manifest=SkillManifest(
                name="svc", description="d", metadata={"version": "2.0.0", "lane": "docs"}
            ),
            path=Path("/tmp/svc"),
        )
    )
    assert r.by_lane("ops") == []  # no stale membership
    assert [s.name for s in r.by_lane("docs")] == ["svc"]
    assert r.lanes() == ["docs"]  # empty 'ops' pruned


def test_example_skill_loads_from_repo() -> None:
    """The repo's skills/_example/ is discoverable."""
    r = SkillRegistry.from_directory(Path("skills"))
    assert "example" in r
    example = r.get("example")
    assert example is not None
    assert example.lane == "documentation"
    assert "example" in example.triggers
