"""Sixth-audit skills fixes: regression tests for `BL-204` / `BL-205` /
`BL-207` / `BL-208` (ADR 0015).

`BL-204` (RecursionError on manifest parse): `parse_skill_md` wrapped
`yaml.safe_load` in `except yaml.YAMLError`, missing `RecursionError`
(PyYAML's deep-mapping overflow). An adversarial SKILL.md now raises
the documented `SkillManifestError` instead of an internal exception.

`BL-205` (MultiDispatcher NaN weights): `MultiDispatcher.__init__` no
longer accepts NaN / -inf / negative weights; the BL-159
``max(0.0, min(1.0, NaN)) == 1.0`` clamp trap is closed at the
construction boundary.

`BL-207` (InstrumentedDispatcher failure telemetry): `dispatch` now
records `calls`, latency, and the `DispatchObserved` event even when
the inner dispatch raises, so `fallback_rate` and the audit stream
surface the failure path instead of silently hiding it.

`BL-208` (Routing-lane meta-skills excluded from routing): the new
`SkillRegistry.routable()` filters out routing-lane skills; every
candidate-iterating dispatcher (`KeywordDispatcher`,
`EmbeddingDispatcher`, `LLMDispatcher`) routes through it, so the
in-tree `dispatcher-skill` and any operator-installed routing meta-
skill cannot be returned as a task recommendation.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
import yaml

from harness.events import HarnessEvent
from skills.dispatcher import Dispatcher
from skills.dispatchers.instrumented import InstrumentedDispatcher
from skills.dispatchers.keyword import KeywordDispatcher
from skills.dispatchers.multi import MultiDispatcher, MultiMode
from skills.errors import SkillManifestError
from skills.loader import parse_skill_md
from skills.registry import SkillRegistry
from skills.types import Skill, SkillManifest, SkillMatch


class _CaptureSink:
    """EventSink double that records every emitted event."""

    def __init__(self) -> None:
        self.events: list[HarnessEvent] = []

    def emit(self, event: HarnessEvent) -> None:
        self.events.append(event)


def _base() -> dict[str, Any]:
    return {
        "workload": "w",
        "contract": "c",
        "contract_version": "v1",
        "trace_id": "t" * 32,
        "span_id": "s" * 16,
    }


def _make_skill(name: str, *, lane: str | None = None, description: str = "") -> Skill:
    """Build a Skill bound to a temporary directory with no body."""
    meta: dict[str, str] = {}
    if lane is not None:
        meta["lane"] = lane
    manifest = SkillManifest(name=name, description=description or name, metadata=meta)
    return Skill(manifest=manifest, path=Path("/nonexistent"))


# --- BL-204: RecursionError on manifest parse -------------------------


def test_parse_skill_md_translates_recursion_error(tmp_path: Path) -> None:
    """An adversarial SKILL.md whose YAML frontmatter nests deeply
    enough to overflow PyYAML's recursion raises `SkillManifestError`,
    not the internal `RecursionError`."""
    # Build a YAML with deep nested mappings. PyYAML's safe_load
    # recurses through nested structures; 3000 levels is well past the
    # default Python recursion limit.
    depth = 3000
    deep_yaml = "name: x\ndescription: y\nmetadata:\n"
    for i in range(depth):
        deep_yaml += "  " + ("  " * i) + f"k{i}:\n"
    deep_yaml += "  " + ("  " * depth) + "leaf: v\n"

    sm = tmp_path / "SKILL.md"
    sm.write_text(f"---\n{deep_yaml}---\nbody\n")

    with pytest.raises(SkillManifestError, match="recursion"):
        parse_skill_md(sm)


def test_parse_skill_md_well_formed_still_parses(tmp_path: Path) -> None:
    """Backward compatibility: a well-formed YAML manifest still
    parses normally. The new `RecursionError` catch does not regress
    the happy path."""
    yml_text = "name: x\ndescription: y\n"
    sm = tmp_path / "SKILL.md"
    sm.write_text(f"---\n{yml_text}---\nbody\n")
    manifest, body = parse_skill_md(sm)
    assert manifest.name == "x"
    assert "body" in body


# --- BL-205: MultiDispatcher finite-weight guard ----------------------


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -0.1])
def test_multi_dispatcher_rejects_bad_weight(bad: float) -> None:
    """NaN, +inf, -inf, and any negative weight are rejected at
    construction. BL-159 class extension: the downstream
    ``max(0.0, min(1.0, score))`` clamp collapses NaN to confidence
    1.0; validating at construction surfaces the bug at the API
    boundary."""
    member = _NoopDispatcher()
    with pytest.raises(ValueError, match="finite"):
        MultiDispatcher(
            members=[member, member],
            mode=MultiMode.WEIGHTED,
            weights=[1.0, bad],
        )


def test_multi_dispatcher_accepts_finite_non_negative_weights() -> None:
    """Zero is allowed (effectively excluding a member from the
    weighted score); positive finite weights pass unchanged."""
    member = _NoopDispatcher()
    MultiDispatcher(members=[member, member], mode=MultiMode.WEIGHTED, weights=[0.0, 1.0])
    MultiDispatcher(members=[member, member], mode=MultiMode.WEIGHTED, weights=[0.5, 0.5])


class _NoopDispatcher:
    """Dispatcher double that returns no matches."""

    name = "noop"

    async def dispatch(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 1,
    ) -> list[SkillMatch]:
        return []


# --- BL-207: InstrumentedDispatcher failure telemetry ----------------


class _FailingDispatcher:
    """Dispatcher double that raises on every call."""

    name = "failing"

    async def dispatch(self, query: str, **_: Any) -> list[SkillMatch]:
        raise RuntimeError("simulated inner failure")


@pytest.mark.asyncio
async def test_instrumented_records_failure_in_stats() -> None:
    """A failing inner dispatch still increments `calls`, records
    latency, and (because top confidence is 0.0 below threshold)
    accumulates a fallback. Pre-`BL-207` `stats` were `(0, 0, [])`."""
    inner = _FailingDispatcher()
    inst = InstrumentedDispatcher(inner)
    with pytest.raises(RuntimeError):
        await inst.dispatch("anything")
    assert inst.stats.calls == 1
    assert len(inst.stats.latencies_ms) == 1
    assert inst.stats.fallbacks == 1
    assert inst.stats.fallback_rate == 1.0


@pytest.mark.asyncio
async def test_instrumented_emits_event_on_failure() -> None:
    """`DispatchObserved` is emitted even when the inner raises (with
    base_event_fields supplied), so an OTel / JSONL operator sees the
    failure rather than a missing event."""
    inner = _FailingDispatcher()
    sink = _CaptureSink()
    inst = InstrumentedDispatcher(inner, sink=sink, base_event_fields=_base())
    with pytest.raises(RuntimeError):
        await inst.dispatch("anything")
    assert len(sink.events) == 1
    evt = sink.events[0]
    assert evt.kind == "dispatch_observed"
    assert evt.matched == 0
    assert evt.fell_back is True


# --- BL-208: Routing-lane meta-skills excluded -----------------------


def test_registry_routable_excludes_routing_lane() -> None:
    """`SkillRegistry.routable()` returns every non-routing-lane
    skill; the dispatcher-skill (and any operator-installed routing
    meta-skill) is excluded."""
    registry = SkillRegistry()
    registry.add(_make_skill("dispatcher-skill", lane="routing"))
    registry.add(_make_skill("shell", lane="ops"))
    registry.add(_make_skill("example", lane="ops"))

    routable = registry.routable()
    names = {s.name for s in routable}
    assert names == {"shell", "example"}


def test_registry_all_still_includes_routing_lane() -> None:
    """`all()` is unchanged (backward compatible): only `routable()`
    applies the filter, so consumers that need the full set (e.g.,
    `SkillBasedDispatcher` reading the routing instructions) still
    see every skill."""
    registry = SkillRegistry()
    registry.add(_make_skill("dispatcher-skill", lane="routing"))
    registry.add(_make_skill("shell", lane="ops"))

    names = {s.name for s in registry.all()}
    assert names == {"dispatcher-skill", "shell"}


@pytest.mark.asyncio
async def test_keyword_dispatcher_does_not_return_routing_skill() -> None:
    """A routing-themed query no longer returns the routing meta-skill
    (which would breach its own SKILL.md "never selected to perform
    user work" contract)."""
    registry = SkillRegistry()
    registry.add(
        _make_skill(
            "dispatcher-skill",
            lane="routing",
            description="A meta skill that routes queries to other skills.",
        )
    )
    registry.add(
        _make_skill("shell", description="Run shell commands."),
    )

    kd = KeywordDispatcher(registry)
    matches = await kd.dispatch("How should I route this query?", limit=3)
    names = {m.skill_name for m in matches}
    assert "dispatcher-skill" not in names


# Quiet ruff F401.
_ = (Dispatcher, yaml)
