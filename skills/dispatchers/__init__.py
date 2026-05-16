"""Reference Dispatcher implementations.

Five reference dispatchers ship with the framework:

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
"""

from skills.dispatchers.chain import RoutingChainDispatcher
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
]
