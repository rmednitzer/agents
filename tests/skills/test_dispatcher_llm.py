"""Tests for LLMDispatcher."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from skills.dispatchers.llm import LLMDispatcher
from skills.errors import DispatchError
from skills.registry import SkillRegistry
from skills.types import Skill, SkillManifest


class _StubRuntime:
    name: str = "stub"

    def __init__(self, response: str) -> None:
        self._response = response

    async def run(self, prompt: str, **kwargs: Any) -> Any:
        return self._response

    def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[Any]:
        raise NotImplementedError


def _skill(name: str, description: str = "d") -> Skill:
    return Skill(
        manifest=SkillManifest(name=name, description=description),
        path=Path("/tmp/" + name),
    )


@pytest.mark.asyncio
async def test_picks_skill_returned_by_runtime() -> None:
    r = SkillRegistry()
    r.add(_skill("foo"))
    r.add(_skill("bar"))
    response = json.dumps([{"skill_name": "foo", "confidence": 0.9, "rationale": "matches"}])
    d = LLMDispatcher(r, _StubRuntime(response))
    matches = await d.dispatch("anything")
    assert len(matches) == 1
    assert matches[0].skill_name == "foo"
    assert matches[0].confidence == 0.9
    assert matches[0].dispatcher == "llm"


@pytest.mark.asyncio
async def test_filters_unknown_skill_names() -> None:
    r = SkillRegistry()
    r.add(_skill("known"))
    response = json.dumps(
        [
            {"skill_name": "unknown", "confidence": 0.9, "rationale": ""},
            {"skill_name": "known", "confidence": 0.7, "rationale": ""},
        ]
    )
    d = LLMDispatcher(r, _StubRuntime(response))
    matches = await d.dispatch("x")
    assert len(matches) == 1
    assert matches[0].skill_name == "known"


@pytest.mark.asyncio
async def test_clamps_confidence_to_range() -> None:
    r = SkillRegistry()
    r.add(_skill("x"))
    response = json.dumps([{"skill_name": "x", "confidence": 1.5, "rationale": ""}])
    d = LLMDispatcher(r, _StubRuntime(response))
    matches = await d.dispatch("q")
    assert matches[0].confidence == 1.0


@pytest.mark.asyncio
async def test_response_without_json_raises() -> None:
    r = SkillRegistry()
    r.add(_skill("x"))
    d = LLMDispatcher(r, _StubRuntime("no JSON here"))
    with pytest.raises(DispatchError):
        await d.dispatch("q")


@pytest.mark.asyncio
async def test_malformed_json_raises() -> None:
    r = SkillRegistry()
    r.add(_skill("x"))
    d = LLMDispatcher(r, _StubRuntime("[not real json"))
    with pytest.raises(DispatchError):
        await d.dispatch("q")


@pytest.mark.asyncio
async def test_empty_registry_returns_empty() -> None:
    d = LLMDispatcher(SkillRegistry(), _StubRuntime("[]"))
    matches = await d.dispatch("q")
    assert matches == []


@pytest.mark.asyncio
async def test_limit_respected() -> None:
    r = SkillRegistry()
    r.add(_skill("a"))
    r.add(_skill("b"))
    response = json.dumps(
        [
            {"skill_name": "a", "confidence": 0.9, "rationale": ""},
            {"skill_name": "b", "confidence": 0.5, "rationale": ""},
        ]
    )
    d = LLMDispatcher(r, _StubRuntime(response))
    matches = await d.dispatch("q", limit=1)
    assert len(matches) == 1
    assert matches[0].skill_name == "a"


@pytest.mark.asyncio
async def test_boolean_confidence_is_rejected() -> None:
    # isinstance(True, int) is True and float(True) == 1.0, so a model
    # emitting `"confidence": true` must be skipped, not treated as a
    # maximally confident match. (regression: skills audit C1)
    r = SkillRegistry()
    r.add(_skill("foo"))
    response = json.dumps([{"skill_name": "foo", "confidence": True, "rationale": ""}])
    d = LLMDispatcher(r, _StubRuntime(response))
    assert await d.dispatch("anything") == []


@pytest.mark.asyncio
async def test_non_finite_confidence_is_rejected() -> None:
    # json.loads accepts NaN/Infinity; max(0.0, min(1.0, nan)) is 1.0,
    # so a non-finite confidence must be skipped, not become a top
    # match. (regression: Copilot review on PR #25)
    r = SkillRegistry()
    r.add(_skill("foo"))
    for token in ("NaN", "Infinity", "-Infinity"):
        response = f'[{{"skill_name": "foo", "confidence": {token}, "rationale": ""}}]'
        d = LLMDispatcher(r, _StubRuntime(response))
        assert await d.dispatch("anything") == []
