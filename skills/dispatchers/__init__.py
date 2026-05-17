"""Reference Dispatcher implementations.

Seven router dispatchers ship (five core at L1; the L2 wave added
MultiDispatcher and EmbeddingDispatcher), plus an InstrumentedDispatcher
telemetry wrapper and the ``default_dispatcher`` composition factory
(ADR 0006, ADR 0007, ADR 0010):

- KeywordDispatcher: deterministic, scores by metadata triggers and
  description token overlap. Zero LLM cost.
- LLMDispatcher: uses a Runtime to pick among candidates. Higher cost,
  handles ambiguity.
- LaneDispatcher: hierarchical. First routes to a lane, then dispatches
  within the lane.
- RoutingChainDispatcher: tries dispatchers in order, returns the first
  match above a confidence threshold. Cheap-first escalation.
- SkillBasedDispatcher: the dispatcher logic itself is a markdown
  skill, loaded and used as the routing prompt to a Runtime.
- MultiDispatcher (BL-050): ensemble that combines several dispatchers
  by vote, average, or weighted blend.
- EmbeddingDispatcher (BL-051): vector similarity between the query and
  skill descriptions via a pluggable EmbeddingProvider.

InstrumentedDispatcher (BL-042) is a wrapper, not a router: it adds
latency, fallback-rate, and telemetry around any of the above.
``default_dispatcher`` (BL-103) composes the recommended instrumented,
cheap-first chain in one call.
"""

from skills.dispatchers.chain import RoutingChainDispatcher
from skills.dispatchers.compose import default_dispatcher
from skills.dispatchers.embedding import EmbeddingDispatcher
from skills.dispatchers.instrumented import DispatchStats, InstrumentedDispatcher
from skills.dispatchers.keyword import KeywordDispatcher
from skills.dispatchers.lane import LaneDispatcher
from skills.dispatchers.llm import LLMDispatcher
from skills.dispatchers.multi import MultiDispatcher, MultiMode
from skills.dispatchers.skill_based import SkillBasedDispatcher

__all__ = [
    "DispatchStats",
    "EmbeddingDispatcher",
    "InstrumentedDispatcher",
    "KeywordDispatcher",
    "LLMDispatcher",
    "LaneDispatcher",
    "MultiDispatcher",
    "MultiMode",
    "RoutingChainDispatcher",
    "SkillBasedDispatcher",
    "default_dispatcher",
]
