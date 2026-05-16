"""Type definitions for the skills framework.

SkillManifest matches the Agent Skills open specification at
https://agentskills.io/specification (frontmatter fields: name,
description, license, compatibility, metadata, allowed-tools).

Framework-specific extensions (lane, triggers, namespace) live in the
spec's open `metadata` field, preserving spec compliance while
supporting the dispatcher framework.

Skill wraps a loaded SkillManifest with lazy body and resource loading.
SkillMatch is what a Dispatcher returns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "Skill",
    "SkillManifest",
    "SkillMatch",
]

# Per Agent Skills spec.
_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_NAME_MAX = 64
_DESCRIPTION_MAX = 1024
_COMPATIBILITY_MAX = 500


class SkillManifest(BaseModel):
    """YAML frontmatter of a SKILL.md file.

    Conforms to https://agentskills.io/specification:

    - name: required, 1-64 chars, lowercase alphanumeric + hyphens, no
      leading/trailing/consecutive hyphens. Must match parent directory
      name (enforced by the loader, not by this model).
    - description: required, 1-1024 chars, describes what + when.
    - license, compatibility (1-500 chars): optional.
    - metadata: open extension point, str -> str.
    - allowed_tools (alias 'allowed-tools'): optional space-separated
      string of pre-approved tools. Experimental per spec.

    Framework-specific extensions in metadata (recognized by name):
    - lane: groups skills for hierarchical dispatch.
    - triggers: comma-separated keywords for KeywordDispatcher.
    - namespace: memory namespace the skill expects to be available.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")

    @model_validator(mode="after")
    def _check_spec(self) -> SkillManifest:
        if not self.name or len(self.name) > _NAME_MAX:
            raise ValueError(f"name must be 1-{_NAME_MAX} characters, got {len(self.name)}")
        if not _NAME_PATTERN.match(self.name):
            raise ValueError(
                f"name {self.name!r} must match lowercase alphanumeric "
                "with hyphens, no leading/trailing/consecutive hyphens"
            )
        if "--" in self.name:
            raise ValueError(f"name {self.name!r} must not contain '--'")
        if not self.description or len(self.description) > _DESCRIPTION_MAX:
            raise ValueError(
                f"description must be 1-{_DESCRIPTION_MAX} characters, got {len(self.description)}"
            )
        if self.compatibility is not None and len(self.compatibility) > _COMPATIBILITY_MAX:
            raise ValueError(f"compatibility must be 1-{_COMPATIBILITY_MAX} chars")
        return self


@dataclass
class Skill:
    """A skill bundle resolved from a directory.

    Manifest is loaded eagerly (cheap, needed for dispatch).
    Body and resources (references, scripts, assets) are loaded lazily
    via the body() method and {references,scripts,assets} attributes.

    The Skill is a mutable wrapper: lazy load updates _body in place.
    The SkillManifest inside is frozen Pydantic.
    """

    manifest: SkillManifest
    path: Path
    _body: str | None = None
    references: dict[str, Path] = field(default_factory=dict)
    scripts: dict[str, Path] = field(default_factory=dict)
    assets: dict[str, Path] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def description(self) -> str:
        return self.manifest.description

    @property
    def lane(self) -> str | None:
        return self.manifest.metadata.get("lane")

    @property
    def triggers(self) -> list[str]:
        raw = self.manifest.metadata.get("triggers", "")
        return [t.strip().lower() for t in raw.split(",") if t.strip()]

    @property
    def allowed_tools(self) -> list[str]:
        """Parsed ``allowed-tools`` declaration.

        The Agent Skills spec encodes allowed-tools as a single
        space-separated string. Returns the tokens in declaration order;
        empty when the skill declares none.
        """
        raw = self.manifest.allowed_tools or ""
        return [t for t in raw.split() if t]

    def body(self) -> str:
        """Lazy-load the SKILL.md body (everything after the frontmatter)."""
        if self._body is None:
            from skills.loader import _read_body_only

            self._body = _read_body_only(self.path / "SKILL.md")
        return self._body


class SkillMatch(BaseModel):
    """A dispatcher's recommendation.

    Attributes:
        skill_name: Name of the matched skill. Resolves via SkillRegistry.get.
        confidence: 0.0 to 1.0; comparable within a single dispatcher,
            not necessarily across dispatchers.
        rationale: Short human-readable explanation. Useful for audit
            and for debugging dispatch surprises.
        dispatcher: Name of the dispatcher that produced this match.
    """

    model_config = ConfigDict(frozen=True)

    skill_name: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    dispatcher: str
