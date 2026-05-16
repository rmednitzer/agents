"""Skill validation helpers.

``validate_allowed_tools`` checks a skill's ``allowed-tools`` declaration
against a harness ToolCatalog (BL-012, ADR 0007). A skill that
pre-approves a tool the harness cannot provide is a configuration error;
surfacing it when the registry is built (or a workload is loaded) beats
discovering it when the tool is first called mid-run.

The split mirrors ``memory.validators``: a non-raising query
(``unknown_tools``) and a raising assertion (``validate_allowed_tools``).
"""

from __future__ import annotations

from harness.tools import ToolCatalog
from skills.errors import SkillManifestError
from skills.registry import SkillRegistry
from skills.types import Skill

__all__ = [
    "unknown_tools",
    "validate_allowed_tools",
    "validate_registry_tools",
]


def unknown_tools(skill: Skill, catalog: ToolCatalog) -> list[str]:
    """Return the skill's allowed-tools absent from the catalog.

    Sorted and deduplicated. An empty list means every declared tool is
    known (or the skill declares no allowed-tools).
    """
    return sorted({t for t in skill.allowed_tools if t not in catalog})


def validate_allowed_tools(skill: Skill, catalog: ToolCatalog) -> None:
    """Raise SkillManifestError if the skill names tools absent from the catalog.

    Raises:
        SkillManifestError: at least one ``allowed-tools`` entry is not
            in ``catalog``. The message lists the offending tools.
    """
    missing = unknown_tools(skill, catalog)
    if missing:
        raise SkillManifestError(
            str(skill.path / "SKILL.md"),
            f"allowed-tools not in harness catalog: {', '.join(missing)}",
        )


def validate_registry_tools(
    registry: SkillRegistry,
    catalog: ToolCatalog,
) -> dict[str, list[str]]:
    """Validate every skill in a registry against the catalog.

    Returns:
        A mapping of skill name -> list of unknown tools, containing only
        skills that declare at least one unknown tool. An empty mapping
        means the whole registry is consistent with the catalog. This
        does not raise: callers decide whether a partial inconsistency is
        fatal (a workload loader may reject; a discovery tool may warn).
    """
    report: dict[str, list[str]] = {}
    for skill in registry.all():
        missing = unknown_tools(skill, catalog)
        if missing:
            report[skill.name] = missing
    return report
