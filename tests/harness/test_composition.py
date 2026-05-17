"""Tests for harness.composition.compose_contracts (BL-060)."""

from __future__ import annotations

import pytest

from harness.composition import compose_contracts
from harness.contract import Contract, Severity, predicate


@predicate(name="shared", severity=Severity.HARD)
def _shared(s: object) -> bool:
    return True


@predicate(name="only_a", severity=Severity.HARD)
def _only_a(s: object) -> bool:
    return True


@predicate(name="only_b", severity=Severity.HARD)
def _only_b(s: object) -> bool:
    return True


@predicate(name="gov_a", severity=Severity.HARD)
def _gov_a(a: object) -> bool:
    return True


@predicate(name="gov_b", severity=Severity.HARD)
def _gov_b(a: object) -> bool:
    return True


def test_empty_raises() -> None:
    with pytest.raises(ValueError, match="at least one"):
        compose_contracts("c", "1.0")


def test_preconditions_intersect_by_name() -> None:
    a: Contract[object, object] = Contract(name="a", version="1", preconditions=[_shared, _only_a])
    b: Contract[object, object] = Contract(name="b", version="1", preconditions=[_shared, _only_b])
    composed = compose_contracts("ab", "2.0", a, b)
    names = {p.name for p in composed.preconditions}
    assert names == {"shared"}  # only the common predicate survives
    assert composed.name == "ab"
    assert composed.version == "2.0"


def test_governance_and_approval_union() -> None:
    a: Contract[object, object] = Contract(
        name="a",
        version="1",
        governance=[_gov_a],
        approval_required=["delete", "shared_tool"],
    )
    b: Contract[object, object] = Contract(
        name="b",
        version="1",
        governance=[_gov_b],
        approval_required=["wipe", "shared_tool"],
    )
    composed = compose_contracts("ab", "1", a, b)
    assert {p.name for p in composed.governance} == {"gov_a", "gov_b"}
    assert composed.approval_required == ["delete", "shared_tool", "wipe"]


def test_single_contract_is_renamed_copy() -> None:
    a: Contract[object, object] = Contract(
        name="a", version="1", preconditions=[_shared, _only_a], governance=[_gov_a]
    )
    composed = compose_contracts("solo", "9", a)
    assert {p.name for p in composed.preconditions} == {"shared", "only_a"}
    assert {p.name for p in composed.governance} == {"gov_a"}


def test_governance_union_dedups_by_name() -> None:
    a: Contract[object, object] = Contract(name="a", version="1", governance=[_gov_a])
    b: Contract[object, object] = Contract(name="b", version="1", governance=[_gov_a])
    composed = compose_contracts("ab", "1", a, b)
    assert len(composed.governance) == 1


# --- A4: strictest severity wins on a name collision -----------------


@predicate(name="shared", severity=Severity.SOFT)
def _shared_soft(s: object) -> bool:
    return True


def test_name_collision_keeps_hard_over_soft() -> None:
    # Workload declares "shared" SOFT; a skill declares it HARD. The
    # composed contract must keep HARD: composition cannot silently
    # downgrade a reviewed obligation (audit A4).
    workload: Contract[object, object] = Contract(
        name="w", version="1", preconditions=[_shared_soft]
    )
    skill: Contract[object, object] = Contract(
        name="s",
        version="1",
        preconditions=[_shared],  # HARD
    )
    composed = compose_contracts("c", "1", workload, skill)
    shared = next(p for p in composed.preconditions if p.name == "shared")
    assert shared.severity == Severity.HARD


def test_name_collision_hard_first_stays_hard() -> None:
    workload: Contract[object, object] = Contract(
        name="w",
        version="1",
        preconditions=[_shared],  # HARD
    )
    skill: Contract[object, object] = Contract(
        name="s",
        version="1",
        preconditions=[_shared_soft],  # SOFT
    )
    composed = compose_contracts("c", "1", workload, skill)
    shared = next(p for p in composed.preconditions if p.name == "shared")
    assert shared.severity == Severity.HARD
