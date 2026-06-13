"""BL-242: graduated authority tiers on the guard.

Covers:

- AuthorityTier ordering (OBSERVE < LOW < STATEFUL < IRREVERSIBLE);
- MappingTierClassifier: known tools, the STATEFUL default for unlisted
  tools, a custom default, Protocol satisfaction;
- HarnessToolGuard with a tier_classifier: no classifier preserves L1
  (tier is None), Tier 0/1 approve with the tier annotated, Tier 2/3
  escalate to REQUIRE_APPROVAL (tier annotated, ApprovalRequested
  emitted), the approval_tier threshold is configurable, the static
  approval_required list still forces approval below the threshold, and
  a hard governance reject pre-empts classification;
- run_under_contract threading: a tier_classifier builds the default
  guard even without governance / approval_required and is wired into it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from harness.authority import AuthorityTier, MappingTierClassifier, TierClassifier
from harness.budgets import BudgetTracker
from harness.contract import Contract, Severity, predicate
from harness.enforcement import run_under_contract
from harness.events import ApprovalRequested
from harness.guard import GuardDecision, HarnessToolGuard, ProposedAction, ToolGuard
from harness.interruption import ResumableState
from harness.mcp import MCPServerSpec
from harness.sinks import MemorySink


def _base() -> dict[str, str]:
    return {
        "workload": "w",
        "contract": "c",
        "contract_version": "0.1.0",
        "trace_id": "trace-1",
        "span_id": "span-1",
    }


_TIERS = {
    "read_metrics": AuthorityTier.OBSERVE,
    "restart_one": AuthorityTier.LOW,
    "deploy": AuthorityTier.STATEFUL,
    "delete_data": AuthorityTier.IRREVERSIBLE,
}


def _classifier(default: AuthorityTier = AuthorityTier.STATEFUL) -> MappingTierClassifier:
    return MappingTierClassifier(_TIERS, default=default)


# --- AuthorityTier -------------------------------------------------------


def test_authority_tier_is_ordered() -> None:
    assert (
        AuthorityTier.OBSERVE
        < AuthorityTier.LOW
        < AuthorityTier.STATEFUL
        < AuthorityTier.IRREVERSIBLE
    )
    assert AuthorityTier.IRREVERSIBLE >= AuthorityTier.STATEFUL


# --- MappingTierClassifier ----------------------------------------------


def test_mapping_classifier_maps_known_tools() -> None:
    c = _classifier()
    assert c.classify("read_metrics", {}) is AuthorityTier.OBSERVE
    assert c.classify("delete_data", {"k": "v"}) is AuthorityTier.IRREVERSIBLE


def test_mapping_classifier_defaults_unlisted_to_stateful() -> None:
    assert _classifier().classify("unknown_tool", {}) is AuthorityTier.STATEFUL


def test_mapping_classifier_custom_default() -> None:
    assert _classifier(default=AuthorityTier.OBSERVE).classify("unknown", {}) is (
        AuthorityTier.OBSERVE
    )


def test_mapping_classifier_satisfies_protocol() -> None:
    assert isinstance(_classifier(), TierClassifier)


# --- guard: tier-driven approval ----------------------------------------


def _guard(**kwargs: Any) -> HarnessToolGuard:
    contract: Contract[None, None] = Contract(name="c", version="0.1.0")
    return HarnessToolGuard(contract, sink=MemorySink(), base_event_fields=_base(), **kwargs)


@pytest.mark.asyncio
async def test_no_classifier_preserves_l1_and_tier_is_none() -> None:
    contract: Contract[None, None] = Contract(name="c", version="0.1.0")
    guard = HarnessToolGuard(contract)
    response = await guard.check("anything", {})
    assert response.decision == GuardDecision.APPROVE
    assert response.tier is None


@pytest.mark.asyncio
async def test_observe_and_low_tiers_approve_with_tier_annotation() -> None:
    guard = _guard(tier_classifier=_classifier())
    obs = await guard.check("read_metrics", {})
    assert obs.decision == GuardDecision.APPROVE
    assert obs.tier is AuthorityTier.OBSERVE
    low = await guard.check("restart_one", {})
    assert low.decision == GuardDecision.APPROVE
    assert low.tier is AuthorityTier.LOW


@pytest.mark.asyncio
async def test_stateful_and_irreversible_require_approval() -> None:
    sink = MemorySink()
    contract: Contract[None, None] = Contract(name="c", version="0.1.0")
    guard = HarnessToolGuard(
        contract, sink=sink, base_event_fields=_base(), tier_classifier=_classifier()
    )
    stateful = await guard.check("deploy", {})
    assert stateful.decision == GuardDecision.REQUIRE_APPROVAL
    assert stateful.tier is AuthorityTier.STATEFUL
    assert stateful.interruption_id is not None
    irr = await guard.check("delete_data", {})
    assert irr.decision == GuardDecision.REQUIRE_APPROVAL
    assert irr.tier is AuthorityTier.IRREVERSIBLE
    approvals = [e for e in sink.events if isinstance(e, ApprovalRequested)]
    assert len(approvals) == 2
    assert {e.tool for e in approvals} == {"deploy", "delete_data"}


@pytest.mark.asyncio
async def test_approval_tier_threshold_is_configurable() -> None:
    # Lowering the threshold to LOW makes a Tier-1 action require approval.
    strict = _guard(tier_classifier=_classifier(), approval_tier=AuthorityTier.LOW)
    assert (await strict.check("restart_one", {})).decision == GuardDecision.REQUIRE_APPROVAL
    # Raising it to IRREVERSIBLE lets a Tier-2 action through.
    lax = _guard(tier_classifier=_classifier(), approval_tier=AuthorityTier.IRREVERSIBLE)
    assert (await lax.check("deploy", {})).decision == GuardDecision.APPROVE
    assert (await lax.check("delete_data", {})).decision == GuardDecision.REQUIRE_APPROVAL


@pytest.mark.asyncio
async def test_static_approval_required_overrides_low_tier() -> None:
    contract: Contract[None, None] = Contract(
        name="c", version="0.1.0", approval_required=["read_metrics"]
    )
    guard = HarnessToolGuard(contract, tier_classifier=_classifier())
    # read_metrics is Tier OBSERVE (would approve) but is on the static list.
    response = await guard.check("read_metrics", {})
    assert response.decision == GuardDecision.REQUIRE_APPROVAL
    assert response.tier is AuthorityTier.OBSERVE


@pytest.mark.asyncio
async def test_hard_governance_preempts_classification() -> None:
    @predicate(name="blocked", severity=Severity.HARD)
    def blocked(action: ProposedAction) -> bool:
        return False

    contract: Contract[None, None] = Contract(name="c", version="0.1.0", governance=[blocked])
    guard = HarnessToolGuard(contract, tier_classifier=_classifier())
    response = await guard.check("delete_data", {})
    assert response.decision == GuardDecision.REJECT
    assert response.tier is None  # classification never runs on a reject


# --- enforcement threading ----------------------------------------------


class _Input(BaseModel):
    query: str


class _Output(BaseModel):
    text: str


class _RecordingRuntime:
    """Runtime that records the guard the harness handed it."""

    name = "recording"

    def __init__(self) -> None:
        self.received_guard: ToolGuard | None = None

    async def run(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
        resume: ResumableState | None = None,
    ) -> Any:
        self.received_guard = guard
        return _Output(text="ok")

    def stream(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
        resume: ResumableState | None = None,
    ) -> AsyncIterator[Any]:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_run_under_contract_threads_classifier_into_default_guard() -> None:
    # A contract with no governance and no approval_required: without a
    # classifier no guard is built, so this proves the classifier both
    # triggers guard construction and is wired into the built guard.
    contract: Contract[_Input, _Output] = Contract(name="c", version="0.1.0")
    runtime = _RecordingRuntime()
    result = await run_under_contract(
        runtime,
        contract,
        _Input(query="hi"),
        _Output,
        tier_classifier=_classifier(),
    )
    assert isinstance(result, _Output)
    assert runtime.received_guard is not None
    deploy = await runtime.received_guard.check("deploy", {})
    assert deploy.decision == GuardDecision.REQUIRE_APPROVAL
    assert deploy.tier is AuthorityTier.STATEFUL
    observe = await runtime.received_guard.check("read_metrics", {})
    assert observe.decision == GuardDecision.APPROVE
