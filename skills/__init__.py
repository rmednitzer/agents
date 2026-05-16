"""agents skills: Agent Skills compatible bundles + dispatcher framework.

Skills follow the Agent Skills open specification (agentskills.io):
directory + SKILL.md (YAML frontmatter + markdown body) + optional
scripts/, references/, assets/.

Framework extensions (lane, triggers, namespace) live in the spec's
open `metadata` field so skills remain spec-compliant.

Five reference dispatchers ship with the framework: KeywordDispatcher,
LLMDispatcher, LaneDispatcher, RoutingChainDispatcher, SkillBasedDispatcher.

See docs/adr/0006-skills-and-dispatcher.md.
"""

from skills.dispatcher import Dispatcher
from skills.dispatchers import (
    KeywordDispatcher,
    LaneDispatcher,
    LLMDispatcher,
    RoutingChainDispatcher,
    SkillBasedDispatcher,
)
from skills.errors import (
    DispatchError,
    NoSkillFound,
    SkillError,
    SkillLoadError,
    SkillManifestError,
)
from skills.loader import discover_skill, parse_skill_md
from skills.registry import SkillRegistry
from skills.types import Skill, SkillManifest, SkillMatch

__all__ = [
    "DispatchError",
    "Dispatcher",
    "KeywordDispatcher",
    "LLMDispatcher",
    "LaneDispatcher",
    "NoSkillFound",
    "RoutingChainDispatcher",
    "Skill",
    "SkillBasedDispatcher",
    "SkillError",
    "SkillLoadError",
    "SkillManifest",
    "SkillManifestError",
    "SkillMatch",
    "SkillRegistry",
    "discover_skill",
    "parse_skill_md",
]
