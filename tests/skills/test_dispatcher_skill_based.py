"""Tests for SkillBasedDispatcher."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from skills.dispatchers.skill_based import SkillBasedDispatcher
from skills.errors import SkillError
from skills.registry import SkillRegistry


def _write_skill(tmp: Path, name: str, body: str = "Body.") -> Path:
    skill_dir = tmp / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill.\n---\n{body}",
        encoding="utf-8",
    )
    return skill_dir


class _StubRuntime:
    name: str = "stub"

    def __init__(self, response: str) -> None:
        self._response = response

    async def run(self, prompt: str, **kwargs: Any) -> Any:
        self.last_prompt = prompt
        return self._response

    def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[Any]:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_skill_based_routes_via_runtime(tmp_path: Path) -> None:
    _write_skill(tmp_path, "dispatcher-skill", "Route to the best match.")
    _write_skill(tmp_path, "foo")
    _write_skill(tmp_path, "bar")
    r = SkillRegistry.from_directory(tmp_path)
    response = json.dumps([{"skill_name": "foo", "confidence": 0.8, "rationale": "match"}])
    runtime = _StubRuntime(response)
    d = SkillBasedDispatcher(r, "dispatcher-skill", runtime)
    matches = await d.dispatch("q")
    assert len(matches) == 1
    assert matches[0].skill_name == "foo"
    assert matches[0].dispatcher == "skill-based"
    assert "Route to the best match." in runtime.last_prompt


@pytest.mark.asyncio
async def test_dispatcher_excludes_itself_from_catalog(tmp_path: Path) -> None:
    _write_skill(tmp_path, "router", "I route.")
    _write_skill(tmp_path, "real")
    r = SkillRegistry.from_directory(tmp_path)
    response = json.dumps([{"skill_name": "real", "confidence": 0.9, "rationale": ""}])
    runtime = _StubRuntime(response)
    d = SkillBasedDispatcher(r, "router", runtime)
    await d.dispatch("q")
    assert "router" not in runtime.last_prompt.split("Catalog:")[-1]


@pytest.mark.asyncio
async def test_versioned_dispatcher_skill_excluded_from_catalog(tmp_path: Path) -> None:
    """A name@version dispatcher_skill is still excluded from its catalog."""
    rd = tmp_path / "router"
    rd.mkdir()
    (rd / "SKILL.md").write_text(
        "---\nname: router\ndescription: I route.\nmetadata:\n  version: 2.0.0\n---\nbody",
        encoding="utf-8",
    )
    _write_skill(tmp_path, "real")
    r = SkillRegistry.from_directory(tmp_path)
    response = json.dumps([{"skill_name": "real", "confidence": 0.9, "rationale": ""}])
    runtime = _StubRuntime(response)
    d = SkillBasedDispatcher(r, "router@2.0.0", runtime)
    matches = await d.dispatch("q")
    assert [m.skill_name for m in matches] == ["real"]
    assert "router" not in runtime.last_prompt.split("Catalog:")[-1]


@pytest.mark.asyncio
async def test_parses_array_amid_noise(tmp_path: Path) -> None:
    """Greedy-regex regression: extra brackets around the array are tolerated."""
    _write_skill(tmp_path, "router", "x")
    _write_skill(tmp_path, "real")
    r = SkillRegistry.from_directory(tmp_path)
    noisy = 'Sure! [note]\n[{"skill_name": "real", "confidence": 0.7, "rationale": "ok"}]\n[done]'
    d = SkillBasedDispatcher(r, "router", _StubRuntime(noisy))
    matches = await d.dispatch("q")
    assert [m.skill_name for m in matches] == ["real"]


def test_dispatcher_skill_not_in_registry_raises(tmp_path: Path) -> None:
    r = SkillRegistry()
    with pytest.raises(SkillError, match="not in registry"):
        SkillBasedDispatcher(r, "missing", _StubRuntime("[]"))


@pytest.mark.asyncio
async def test_filters_unknown_skill_names(tmp_path: Path) -> None:
    _write_skill(tmp_path, "router", "x")
    _write_skill(tmp_path, "real")
    r = SkillRegistry.from_directory(tmp_path)
    response = json.dumps(
        [
            {"skill_name": "ghost", "confidence": 0.9, "rationale": ""},
            {"skill_name": "real", "confidence": 0.7, "rationale": ""},
        ]
    )
    d = SkillBasedDispatcher(r, "router", _StubRuntime(response))
    matches = await d.dispatch("q")
    assert len(matches) == 1
    assert matches[0].skill_name == "real"


@pytest.mark.asyncio
async def test_boolean_confidence_is_rejected(tmp_path: Path) -> None:
    # `"confidence": true` must not become a 1.0-confidence match.
    # (regression: skills audit C1)
    _write_skill(tmp_path, "router", "I route.")
    _write_skill(tmp_path, "real")
    r = SkillRegistry.from_directory(tmp_path)
    response = json.dumps([{"skill_name": "real", "confidence": True, "rationale": ""}])
    d = SkillBasedDispatcher(r, "router", _StubRuntime(response))
    assert await d.dispatch("q") == []
