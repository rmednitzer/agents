"""Skill loader: parse SKILL.md, discover resources, validate.

Per the Agent Skills spec:
- Directory name must match the manifest name field.
- SKILL.md starts with YAML frontmatter delimited by '---' lines.
- Optional subdirectories: scripts/, references/, assets/.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from skills.errors import SkillLoadError, SkillManifestError
from skills.types import Skill, SkillManifest

__all__ = [
    "discover_skill",
    "parse_skill_md",
]


def parse_skill_md(path: Path) -> tuple[SkillManifest, str]:
    """Parse a SKILL.md file into (manifest, body).

    Args:
        path: Path to the SKILL.md file.

    Returns:
        Tuple of (parsed SkillManifest, raw markdown body).

    Raises:
        SkillLoadError: File missing or unreadable.
        SkillManifestError: Frontmatter parse or validation failed.
    """
    if not path.is_file():
        raise SkillLoadError(str(path), "SKILL.md not found")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillLoadError(str(path), f"read failed: {exc}") from exc

    frontmatter_text, body = _split_frontmatter(text, path)

    try:
        raw = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        raise SkillManifestError(str(path), f"YAML parse: {exc}") from exc

    if not isinstance(raw, dict):
        raise SkillManifestError(
            str(path), f"frontmatter must be a mapping, got {type(raw).__name__}"
        )

    try:
        manifest = SkillManifest.model_validate(raw)
    except ValidationError as exc:
        raise SkillManifestError(str(path), str(exc)) from exc

    return manifest, body


def _read_body_only(path: Path) -> str:
    """Read SKILL.md and return only the body (skip frontmatter).

    Used by Skill.body() for lazy loading. Re-parses the file rather
    than relying on shared state because the Skill caches it.
    """
    text = path.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text, path)
    return body


def _split_frontmatter(text: str, path: Path) -> tuple[str, str]:
    """Split a markdown file into (frontmatter_yaml, body).

    The frontmatter must start at line 1 with '---' and end with '---'
    on its own line. The body is everything after.
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        raise SkillManifestError(str(path), "SKILL.md is empty")
    if lines[0].rstrip() != "---":
        raise SkillManifestError(
            str(path),
            "SKILL.md must start with '---' frontmatter delimiter on line 1",
        )

    closing_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            closing_idx = i
            break

    if closing_idx is None:
        raise SkillManifestError(str(path), "closing '---' delimiter not found")

    frontmatter_text = "".join(lines[1:closing_idx])
    body = "".join(lines[closing_idx + 1 :])
    return frontmatter_text, body


def discover_skill(skill_dir: Path) -> Skill:
    """Discover a single skill from its directory.

    Args:
        skill_dir: Directory containing SKILL.md and optional
            scripts/, references/, assets/ subdirectories.

    Returns:
        A Skill with eagerly-loaded manifest and discovered resource
        maps; the body is lazy.

    Raises:
        SkillLoadError: Directory or SKILL.md missing.
        SkillManifestError: Frontmatter invalid or name mismatches
            directory name.
    """
    if not skill_dir.is_dir():
        raise SkillLoadError(str(skill_dir), "not a directory")

    manifest, _body = parse_skill_md(skill_dir / "SKILL.md")

    if manifest.name != skill_dir.name:
        raise SkillManifestError(
            str(skill_dir / "SKILL.md"),
            f"name {manifest.name!r} does not match directory name {skill_dir.name!r}",
        )

    references = _discover_resources(skill_dir / "references")
    scripts = _discover_resources(skill_dir / "scripts")
    assets = _discover_resources(skill_dir / "assets")

    return Skill(
        manifest=manifest,
        path=skill_dir,
        references=references,
        scripts=scripts,
        assets=assets,
    )


def _discover_resources(directory: Path) -> dict[str, Path]:
    """Build a name -> Path map of files in a resource directory.

    Returns an empty dict if the directory doesn't exist.
    """
    if not directory.is_dir():
        return {}
    return {entry.name: entry for entry in directory.iterdir() if entry.is_file()}
