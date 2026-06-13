"""Evidence-capture hook around an irreversible action's execution (BL-253).

The held-out behavioural half of BL-252 (ADR 0033). Graduated authority
(BL-242, ADR 0029) classifies a tool call's blast radius; BL-251 (ADR
0031) carries the tier and a proposed rollback plan onto the human-facing
approval; BL-252 (ADR 0033) makes a Tier 3 (IRREVERSIBLE) approval
require the parameters restated. This module adds the last
operator-gateway pattern from the Vertex MCP analysis
(``docs/analysis/vertex-mcp-lessons.md``): capturing evidence around the
*execution* of an approved irreversible action, so the audit trail holds
the pre- and post-state of a high-blast change (what a file tree, a
database row, or a key version looked like before and after a deletion or
a rotation).

The hook is a workload-supplied ``EvidenceHook`` (the framework binds no
domain knowledge, ADR 0001, the ``TierClassifier`` / ``RollbackPlanner``
stance): ``before`` is awaited immediately before an approved Tier 3 tool
body runs and ``after`` immediately after, around the body only. The
runtime fires it only for an ``IRREVERSIBLE`` action and only when a hook
is configured; every other tool call is the prior path unchanged
(additive to L1, ADR 0007). ``before`` returns an opaque token handed
back to ``after`` so concurrent Tier 3 bodies pair without sharing state,
and ``after`` always runs (in a ``finally``) with the body's exception
(``None`` on success), so an irreversible action that raised is still
recorded. ``RecordingEvidenceHook`` is the deterministic in-tree
reference; a production hook snapshots external state instead.

What this is not: it does not execute a rollback (that stays the
``RollbackPlanner``'s descriptive plan plus a human or workload
decision), it does not gate execution (the Tier 3 approval and the BL-252
restatement already did), and it captures the harness-visible call
context, not the tool's return value (post-state is the hook's own
snapshot, decoupled from the tool's output shape).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from harness.authority import AuthorityTier

__all__ = [
    "EvidenceContext",
    "EvidenceHook",
    "EvidenceRecord",
    "RecordingEvidenceHook",
]


@dataclass(frozen=True)
class EvidenceContext:
    """The call context passed to an ``EvidenceHook.before`` (BL-253).

    Immutable. ``tier`` is always ``IRREVERSIBLE`` (the hook fires only
    for Tier 3); it is carried explicitly so a hook that also logs lower
    tiers reads one shape. ``tool_call_id`` is the framework's stable
    per-call id on the deferred and MCP paths and ``None`` on the
    replay-local path (which has no per-call id). ``rollback_plan`` is the
    plan a ``RollbackPlanner`` produced for this action (BL-251), or
    ``None`` when no planner is configured.
    """

    tool: str
    arguments: dict[str, Any]
    tier: AuthorityTier
    tool_call_id: str | None = None
    rollback_plan: str | None = None


@runtime_checkable
class EvidenceHook(Protocol):
    """Captures pre/post evidence around an irreversible action (BL-253).

    Supplied by the workload. ``before`` is awaited immediately before an
    approved Tier 3 tool body executes and returns an opaque token;
    ``after`` is awaited immediately after the body (in a ``finally``, so
    it runs even when the body raised) and receives that token plus the
    body's exception (``None`` on success). The token pairs a ``before``
    with its ``after`` without the hook keying on ``tool_call_id``, so
    concurrent Tier 3 calls do not interleave (a hook needing the context
    in ``after`` returns it, or state derived from it, as the token).
    Both are confined to evidence capture: they change neither the
    decision (approval already happened) nor the tool's result.
    """

    async def before(self, context: EvidenceContext) -> Any: ...
    async def after(self, token: Any, *, error: BaseException | None = None) -> None: ...


@dataclass(frozen=True)
class EvidenceRecord:
    """One ``RecordingEvidenceHook`` entry: a before or after phase line."""

    phase: str  # "before" | "after"
    tool: str
    tier: AuthorityTier
    tool_call_id: str | None = None
    rollback_plan: str | None = None
    error: str | None = None  # repr of the body exception; "after" only


class RecordingEvidenceHook:
    """In-memory ``EvidenceHook`` reference (BL-253).

    The deterministic in-tree reference (the ``MappingTierClassifier`` /
    ``MappingRollbackPlanner`` stance): it appends an ``EvidenceRecord``
    for each ``before`` / ``after`` to ``records`` and uses the index of
    the ``before`` entry as the token, so ``after`` reads its paired
    ``before`` back through the token. A production hook snapshots
    external state (a file tree, a database row, git HEAD) and writes it
    to durable audit storage instead; this one is for tests and as a
    template.
    """

    def __init__(self) -> None:
        self.records: list[EvidenceRecord] = []

    async def before(self, context: EvidenceContext) -> int:
        self.records.append(
            EvidenceRecord(
                phase="before",
                tool=context.tool,
                tier=context.tier,
                tool_call_id=context.tool_call_id,
                rollback_plan=context.rollback_plan,
            )
        )
        return len(self.records) - 1

    async def after(self, token: Any, *, error: BaseException | None = None) -> None:
        paired = self.records[token]
        self.records.append(
            EvidenceRecord(
                phase="after",
                tool=paired.tool,
                tier=paired.tier,
                tool_call_id=paired.tool_call_id,
                rollback_plan=paired.rollback_plan,
                error=None if error is None else repr(error),
            )
        )
