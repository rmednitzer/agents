"""Tests for KeywordDispatcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from skills.dispatchers.keyword import KeywordDispatcher
from skills.registry import SkillRegistry
from skills.types import Skill, SkillManifest


def _skill(name: str, description: str, triggers: str = "", lane: str | None = None) -> Skill:
    metadata = {"triggers": triggers}
    if lane:
        metadata["lane"] = lane
    return Skill(
        manifest=SkillManifest(name=name, description=description, metadata=metadata),
        path=Path("/tmp/" + name),
    )


@pytest.mark.asyncio
async def test_empty_registry_returns_empty() -> None:
    d = KeywordDispatcher(SkillRegistry())
    matches = await d.dispatch("anything")
    assert matches == []


@pytest.mark.asyncio
async def test_trigger_match() -> None:
    r = SkillRegistry()
    r.add(
        _skill(
            "deploy",
            "Deploy the application.",
            triggers="deploy, push, ship",
        )
    )
    r.add(_skill("query", "Query the database.", triggers="query, sql"))
    d = KeywordDispatcher(r)
    matches = await d.dispatch("How do I deploy this?")
    assert len(matches) == 1
    assert matches[0].skill_name == "deploy"
    assert matches[0].dispatcher == "keyword"
    assert "deploy" in matches[0].rationale


@pytest.mark.asyncio
async def test_no_match_returns_empty() -> None:
    r = SkillRegistry()
    r.add(_skill("zzz", "zzz", triggers="zebra"))
    d = KeywordDispatcher(r)
    matches = await d.dispatch("Tell me about giraffes.")
    assert matches == []


@pytest.mark.asyncio
async def test_description_overlap_scoring() -> None:
    r = SkillRegistry()
    r.add(_skill("a", "Manage Postgres database backups."))
    r.add(_skill("b", "Schedule cron jobs."))
    d = KeywordDispatcher(r)
    matches = await d.dispatch("Postgres backups")
    assert len(matches) == 1
    assert matches[0].skill_name == "a"


@pytest.mark.asyncio
async def test_limit_respected() -> None:
    r = SkillRegistry()
    r.add(_skill("a", "Apple.", triggers="fruit"))
    r.add(_skill("b", "Banana.", triggers="fruit"))
    r.add(_skill("c", "Cherry.", triggers="fruit"))
    d = KeywordDispatcher(r)
    matches = await d.dispatch("fruit", limit=2)
    assert len(matches) == 2


@pytest.mark.asyncio
async def test_zero_limit_returns_empty() -> None:
    r = SkillRegistry()
    r.add(_skill("a", "a", triggers="x"))
    d = KeywordDispatcher(r)
    matches = await d.dispatch("x", limit=0)
    assert matches == []


@pytest.mark.asyncio
async def test_trigger_outscores_description() -> None:
    r = SkillRegistry()
    r.add(
        _skill(
            "trigger-match",
            "Other words entirely.",
            triggers="deploy",
        )
    )
    r.add(
        _skill(
            "description-match",
            "Deploy the deploy deployment.",
            triggers="",
        )
    )
    d = KeywordDispatcher(r)
    matches = await d.dispatch("deploy now", limit=2)
    # trigger match should score higher
    assert matches[0].skill_name == "trigger-match"


@pytest.mark.asyncio
async def test_confidence_normalized_to_top() -> None:
    r = SkillRegistry()
    r.add(_skill("strong", "deploy deploy", triggers="deploy"))
    r.add(_skill("weak", "deployment word", triggers=""))
    d = KeywordDispatcher(r)
    matches = await d.dispatch("deploy", limit=2)
    # Top match has confidence 1.0
    assert matches[0].confidence == 1.0
    if len(matches) > 1:
        assert matches[1].confidence <= 1.0
