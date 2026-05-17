"""Recommended default dispatcher composition (BL-103).

ADR 0006 recommends a cheap-first routing chain wrapped in telemetry,
but assembling it (KeywordDispatcher -> optional LLMDispatcher, inside a
RoutingChainDispatcher, inside an InstrumentedDispatcher) was left to
every caller. ``default_dispatcher`` builds exactly that, so the
instrumented, fallback-aware composition is the one-call default rather
than a pattern each workload re-derives.
"""

from __future__ import annotations

from typing import Any

from skills.dispatcher import Dispatcher
from skills.dispatchers.chain import RoutingChainDispatcher
from skills.dispatchers.embedding import EmbeddingDispatcher
from skills.dispatchers.instrumented import InstrumentedDispatcher
from skills.dispatchers.keyword import KeywordDispatcher
from skills.dispatchers.llm import LLMDispatcher
from skills.embeddings import EmbeddingProvider
from skills.registry import SkillRegistry

__all__ = ["default_dispatcher"]


def default_dispatcher(
    registry: SkillRegistry,
    *,
    runtime: Any | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    sink: Any | None = None,
    base_event_fields: dict[str, Any] | None = None,
    threshold: float = 0.6,
) -> InstrumentedDispatcher:
    """Build the recommended instrumented, cheap-first dispatch chain.

    The chain is tried in cost order and the first tier whose top
    confidence reaches ``threshold`` wins:

    1. ``KeywordDispatcher`` (deterministic, zero model cost) -- always.
    2. ``EmbeddingDispatcher`` -- only if ``embedding_provider`` is given.
    3. ``LLMDispatcher`` -- only if ``runtime`` is given (the costly
       fallback for genuinely ambiguous queries).

    The whole chain is wrapped in ``InstrumentedDispatcher`` so latency,
    fallback rate, and ``DispatchObserved`` events are emitted with the
    same ``threshold``. With neither ``runtime`` nor
    ``embedding_provider`` this is an instrumented keyword router, which
    is model-free and safe for the CLI / CI.
    """
    chain: list[Dispatcher] = [KeywordDispatcher(registry)]
    if embedding_provider is not None:
        chain.append(EmbeddingDispatcher(registry, embedding_provider))
    if runtime is not None:
        chain.append(LLMDispatcher(registry, runtime))
    inner: Dispatcher = (
        chain[0] if len(chain) == 1 else RoutingChainDispatcher(chain, threshold=threshold)
    )
    return InstrumentedDispatcher(
        inner,
        sink=sink,
        base_event_fields=base_event_fields,
        threshold=threshold,
    )
