"""Graduated authority tiers for tool-call governance (BL-242).

The default guard's APPROVE / REJECT / REQUIRE_APPROVAL decision is flat:
a tool is either on the contract's ``approval_required`` list or it is
not. Graduated authority adds an orthogonal axis keyed to a proposed
action's reversibility and blast radius, the operator-gateway pattern
from the Vertex MCP analysis (``docs/analysis/vertex-mcp-lessons.md``): a
read-only query needs no approval, a reversible low-blast change can act
and be logged, a stateful or irreversible change must be confirmed
first. The model's job is correct tier classification; the substrate
makes the tier drive the approval requirement.

This module ships the taxonomy (``AuthorityTier``), the
``TierClassifier`` Protocol (workload-supplied, like memory's
``Embedder`` or the dispatcher's lanes, so the framework binds no domain
knowledge, ADR 0001), and the deterministic ``MappingTierClassifier``
reference. ``HarnessToolGuard`` consumes a classifier to escalate a
Tier 2-or-above action to REQUIRE_APPROVAL beyond the static
``approval_required`` list (ADR 0029). The rollback-plan and
evidence-capture refinements on the Tier 2 / 3 approval context are
tracked forward (``BL-251``).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import IntEnum
from typing import Any, Protocol, runtime_checkable

__all__ = ["AuthorityTier", "MappingTierClassifier", "TierClassifier"]


class AuthorityTier(IntEnum):
    """A proposed action's authority tier, by reversibility / blast radius.

    Ordered (an ``IntEnum``) so a guard can gate "this tier or above
    requires approval" with one comparison. The four tiers mirror the
    operator-gateway graduated-autonomy model:

    - ``OBSERVE`` (0): read-only. Nothing changes state, so no approval
      (log queries, status reads, metrics).
    - ``LOW`` (1): reversible, low blast. Self-healing or trivially
      undone (restart one container, clear a cache); the model may act
      and the action is logged.
    - ``STATEFUL`` (2): stateful, visible impact. A change a user or a
      dependent system would notice (a config change, a deploy); confirm
      before execution.
    - ``IRREVERSIBLE`` (3): irreversible, high blast. Rollback is
      expensive or impossible (data deletion, key rotation); confirm
      with the parameters restated and evidence captured.
    """

    OBSERVE = 0
    LOW = 1
    STATEFUL = 2
    IRREVERSIBLE = 3


@runtime_checkable
class TierClassifier(Protocol):
    """Maps a proposed tool call to an ``AuthorityTier`` (BL-242).

    Supplied by the workload (the framework binds no domain knowledge,
    ADR 0001, the ``Embedder`` / dispatcher stance). ``classify`` must be
    pure and total: it returns a tier for every ``(tool, arguments)``,
    with no side effects. A model-driven classifier (the model assesses
    blast radius per call) satisfies the same Protocol and is the
    workload's choice; the in-tree ``MappingTierClassifier`` is the
    deterministic name-based reference.
    """

    def classify(self, tool: str, arguments: dict[str, Any]) -> AuthorityTier: ...


class MappingTierClassifier:
    """A ``TierClassifier`` keyed on tool name (BL-242).

    The deterministic in-tree reference (the ``KeywordDispatcher`` /
    ``HashingEmbeddingProvider`` stance): a static tool-name-to-tier map
    with a ``default`` for any unlisted tool. The default is
    ``STATEFUL`` (Tier 2, which requires approval under the guard's
    default threshold): an unclassified tool is treated as needing
    confirmation, the fail-safe posture ("when unsure, ask"). Arguments
    are ignored here; a classifier that inspects them (a delete with a
    wildcard is higher-blast than one scoped to a single key) implements
    the Protocol directly.
    """

    def __init__(
        self,
        tiers: Mapping[str, AuthorityTier],
        *,
        default: AuthorityTier = AuthorityTier.STATEFUL,
    ) -> None:
        self._tiers = dict(tiers)
        self._default = default

    def classify(self, tool: str, arguments: dict[str, Any]) -> AuthorityTier:
        return self._tiers.get(tool, self._default)
