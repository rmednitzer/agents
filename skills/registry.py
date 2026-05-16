"""SkillRegistry: index of skills available for dispatch.

Eager manifest load (cheap, needed for dispatch). Lazy body load (via
Skill.body() on first call). Lane index for hierarchical dispatch.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from skills.loader import discover_skill
from skills.types import Skill

__all__ = ["SkillRegistry"]


class SkillRegistry:
    """Indexed collection of skills.

    Construct via from_directory(root) to discover skills in a tree, or
    via add() for programmatic construction (useful in tests).
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._lanes: dict[str, list[str]] = {}
        # name -> version -> Skill (BL-053). Insertion order is retained
        # so "current" == most recently added, matching L1 last-write-wins.
        self._versions: dict[str, dict[str, Skill]] = {}

    @classmethod
    def from_directory(cls, root: Path) -> SkillRegistry:
        """Discover every directory under `root` containing a SKILL.md.

        Args:
            root: Directory to scan. Each immediate subdirectory with a
                SKILL.md becomes a skill.

        Returns:
            A populated SkillRegistry. Directories without SKILL.md are
            silently skipped.
        """
        registry = cls()
        if not root.is_dir():
            return registry
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if not (entry / "SKILL.md").is_file():
                continue
            registry.add(discover_skill(entry))
        return registry

    def add(self, skill: Skill) -> None:
        """Add a skill. Distinct versions coexist (BL-053).

        Same name + same version overwrites (last-write-wins, the L1
        behaviour for unversioned skills). A different version is kept
        alongside; the most recently added version becomes "current"
        (what ``get(name)`` and iteration return).
        """
        self._skills[skill.name] = skill
        versions = self._versions.setdefault(skill.name, {})
        # Re-insert so this version is last (current) in iteration order.
        versions.pop(skill.version, None)
        versions[skill.version] = skill
        lane = skill.lane
        if lane is not None:
            existing = self._lanes.setdefault(lane, [])
            if skill.name not in existing:
                existing.append(skill.name)

    def get(self, name: str) -> Skill | None:
        """Resolve a skill. ``name`` or ``name@version`` (BL-053).

        Bare name returns the current (most recently added) version.
        ``name@version`` returns that exact version, or None.
        """
        if "@" in name:
            base, _, version = name.partition("@")
            return self._versions.get(base, {}).get(version)
        return self._skills.get(name)

    def versions(self, name: str) -> list[str]:
        """Versions registered for ``name``, in registration order."""
        return list(self._versions.get(name, {}).keys())

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def by_lane(self, lane: str) -> list[Skill]:
        names = self._lanes.get(lane, [])
        return [self._skills[n] for n in names if n in self._skills]

    def lanes(self) -> list[str]:
        return sorted(self._lanes.keys())

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._skills

    def __iter__(self) -> Iterator[Skill]:
        return iter(self._skills.values())
