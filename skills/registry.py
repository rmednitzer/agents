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
        """Add a skill to the registry. Last-write-wins on name conflict."""
        self._skills[skill.name] = skill
        lane = skill.lane
        if lane is not None:
            existing = self._lanes.setdefault(lane, [])
            if skill.name not in existing:
                existing.append(skill.name)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

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
