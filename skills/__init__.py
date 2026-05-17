"""agents skills: Agent Skills compatible bundles + dispatcher framework.

Skills follow the Agent Skills open specification (agentskills.io):
directory + SKILL.md (YAML frontmatter + markdown body) + optional
scripts/, references/, assets/.

Framework extensions (lane, triggers, namespace) live in the spec's
open `metadata` field so skills remain spec-compliant.

Eight dispatchers ship: the five core routers (KeywordDispatcher,
LLMDispatcher, LaneDispatcher, RoutingChainDispatcher,
SkillBasedDispatcher) plus the L2 MultiDispatcher and
EmbeddingDispatcher, and InstrumentedDispatcher wraps any of them with
telemetry. See skills/dispatchers for the per-dispatcher contract.

See docs/adr/0006-skills-and-dispatcher.md.
"""

from skills.dispatcher import Dispatcher
from skills.dispatchers import (
    DispatchStats,
    EmbeddingDispatcher,
    InstrumentedDispatcher,
    KeywordDispatcher,
    LaneDispatcher,
    LLMDispatcher,
    MultiDispatcher,
    MultiMode,
    RoutingChainDispatcher,
    SkillBasedDispatcher,
)
from skills.embeddings import EmbeddingProvider, cosine_similarity
from skills.errors import (
    DispatchError,
    NoSkillFound,
    SkillError,
    SkillLoadError,
    SkillManifestError,
)
from skills.loader import discover_skill, parse_skill_md
from skills.registry import SkillRegistry
from skills.sources import (
    GitHubSkillSource,
    LocalSkillSource,
    SkillSource,
    install_skill,
)
from skills.types import Skill, SkillManifest, SkillMatch
from skills.validators import (
    unknown_tools,
    validate_allowed_tools,
    validate_registry_tools,
)

__all__ = [
    "DispatchError",
    "DispatchStats",
    "Dispatcher",
    "EmbeddingDispatcher",
    "EmbeddingProvider",
    "GitHubSkillSource",
    "InstrumentedDispatcher",
    "KeywordDispatcher",
    "LLMDispatcher",
    "LaneDispatcher",
    "LocalSkillSource",
    "MultiDispatcher",
    "MultiMode",
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
    "SkillSource",
    "cosine_similarity",
    "discover_skill",
    "install_skill",
    "parse_skill_md",
    "unknown_tools",
    "validate_allowed_tools",
    "validate_registry_tools",
]
