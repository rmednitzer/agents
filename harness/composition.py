"""Contract composition (BL-060, BL-052; ADR 0002, ADR 0007).

When a workload loads a skill that ships its own contract
(``skills/<name>/contract.py``), the two contracts must combine into one
enforceable contract. ADR 0002 fixed the rule; this implements it:

- preconditions / invariants / postconditions: **intersection by
  predicate name**. A behavioural guarantee is only promised if *every*
  composed contract asserts it -- composing must not silently weaken a
  caller by importing obligations they never reviewed, nor strengthen
  one beyond what all parties agreed. The predicate object is taken from
  the first contract that declares the name.
- governance: **union** (dedup by name, declaration order preserved).
  Action policy is safety-critical: every party's veto must apply.
- approval_required: **union**. If any party requires human approval for
  a tool, the composed contract requires it.

This is the Bhardwaj (P, I, G, R) tuple composed conservatively:
capability obligations intersect, safety obligations union.
"""

from __future__ import annotations

from typing import Any

from harness.contract import Contract, Predicate, Severity

__all__ = ["compose_contracts"]


def _intersect_by_name(
    groups: list[list[Predicate[Any]]],
) -> list[Predicate[Any]]:
    """Predicates whose name appears in every group, strictest kept.

    A name must appear in *every* group to survive (intersection: a
    guarantee only holds if every party asserts it). When parties
    declare the same name at different severities, the HARD instance is
    kept: composition must not silently downgrade a reviewed HARD
    obligation to SOFT because another party happened to declare it
    soft. Declaration order (first group) is preserved for the output.
    """
    if not groups:
        return []
    common: set[str] = set()
    for i, group in enumerate(groups):
        names = {p.name for p in group}
        common = names if i == 0 else (common & names)
    # Strictest instance per shared name across all groups.
    strictest: dict[str, Predicate[Any]] = {}
    for group in groups:
        for p in group:
            if p.name not in common:
                continue
            chosen = strictest.get(p.name)
            if chosen is None or (chosen.severity != Severity.HARD and p.severity == Severity.HARD):
                strictest[p.name] = p
    out: list[Predicate[Any]] = []
    seen: set[str] = set()
    for p in groups[0]:
        if p.name in common and p.name not in seen:
            seen.add(p.name)
            out.append(strictest[p.name])
    return out


def _union_by_name(groups: list[list[Predicate[Any]]]) -> list[Predicate[Any]]:
    """All predicates across groups, deduplicated by name, order preserved."""
    out: list[Predicate[Any]] = []
    seen: set[str] = set()
    for group in groups:
        for p in group:
            if p.name not in seen:
                seen.add(p.name)
                out.append(p)
    return out


def compose_contracts(
    name: str,
    version: str,
    *contracts: Contract[Any, Any],
) -> Contract[Any, Any]:
    """Compose contracts per the ADR 0002 rule.

    Args:
        name: Identity for the composed contract.
        version: Version for the composed contract.
        *contracts: Contracts to compose (workload + zero or more skill
            contracts). At least one is required.

    Returns:
        A new Contract: pre/invariant/post predicates intersected by
        name, governance and approval_required unioned. Composing a
        single contract returns an equivalent renamed copy.

    Raises:
        ValueError: no contracts were supplied.
    """
    if not contracts:
        raise ValueError("compose_contracts requires at least one contract")

    approval: list[str] = []
    seen_approval: set[str] = set()
    for c in contracts:
        for tool in c.approval_required:
            if tool not in seen_approval:
                seen_approval.add(tool)
                approval.append(tool)

    return Contract(
        name=name,
        version=version,
        preconditions=_intersect_by_name([list(c.preconditions) for c in contracts]),
        invariants=_intersect_by_name([list(c.invariants) for c in contracts]),
        postconditions=_intersect_by_name([list(c.postconditions) for c in contracts]),
        governance=_union_by_name([list(c.governance) for c in contracts]),
        approval_required=approval,
    )
