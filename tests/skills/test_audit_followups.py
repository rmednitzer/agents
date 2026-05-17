"""Regression tests for the ADR 0011 audit follow-ups (skills).

- BL-172: a pre-existing ``dest/<name>`` symlink must not let a network
  SkillSource escape the install directory (the GitHub/Marketplace twin
  of the BL-169 LocalSkillSource symlink hole).
- BL-173: ``_balanced_spans`` stays linear on deeply nested model
  output and ``first_json_array`` never lets ``RecursionError`` escape.
"""

from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest

from skills.dispatchers._json import first_json_array
from skills.sources import GitHubSkillSource, MarketplaceSkillSource, install_skill

_SKILL_MD = "---\nname: {n}\ndescription: A test skill bundle for {n}.\n---\nBody.\n"


def _make_tar_gz(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in entries.items():
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _FakeResp:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def getheader(self, name: str) -> str | None:
        return None

    def read(self, amt: int = -1) -> bytes:
        if amt < 0:
            chunk, self._data = self._data, b""
            return chunk
        chunk, self._data = self._data[:amt], self._data[amt:]
        return chunk


def _patch(monkeypatch: Any, data: bytes) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResp(data))


# --- BL-172: network-source symlink escape -----------------------------


def test_github_source_preexisting_symlink_does_not_escape(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A ``dest/<name>`` symlink to an outside dir must not be followed.

    The old ``(dest / name).resolve()`` before ``shutil.rmtree`` followed
    the link, deleting the target's contents and extracting members
    fully outside ``dest``. The fix unlinks the link itself first, then
    resolves and asserts containment, so the outside tree is untouched
    and the bundle installs inside ``dest``.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("UNTOUCHED")

    dest = tmp_path / "installed"
    dest.mkdir()
    (dest / "cool").symlink_to(outside, target_is_directory=True)

    archive = _make_tar_gz(
        {"skills-main/skills/cool/SKILL.md": _SKILL_MD.format(n="cool").encode()}
    )
    _patch(monkeypatch, archive)
    src = GitHubSkillSource(repo="anthropics/skills", ref="main", path_prefix="skills")
    skill = install_skill(src, "cool", dest)

    # Outside tree intact; nothing written or deleted there.
    assert secret.read_text() == "UNTOUCHED"
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]
    # Bundle landed inside dest, and dest/cool is now a real directory.
    assert skill.name == "cool"
    target = dest / "cool"
    assert target.is_dir()
    assert not target.is_symlink()
    assert (target / "SKILL.md").is_file()


def test_marketplace_source_preexisting_symlink_does_not_escape(
    tmp_path: Path, monkeypatch: Any
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("KEEP")

    dest = tmp_path / "installed"
    dest.mkdir()
    (dest / "tool").symlink_to(outside, target_is_directory=True)

    archive = _make_tar_gz({"tool/SKILL.md": _SKILL_MD.format(n="tool").encode()})
    _patch(monkeypatch, archive)
    src = MarketplaceSkillSource("https://example.test/{name}.tar.gz", strip_components=0)
    skill = install_skill(src, "tool", dest)

    assert (outside / "keep.txt").read_text() == "KEEP"
    assert not (outside / "SKILL.md").exists()
    assert skill.name == "tool"
    assert (dest / "tool").is_dir()
    assert not (dest / "tool").is_symlink()


# --- BL-173: nested-array linearity + RecursionError safety ------------


def test_balanced_spans_linear_on_deep_nesting() -> None:
    """Nested ``[``xN ``]``xN must not be O(n^2) in time or memory.

    The prior code materialised a growing slice on every nested ``]``
    (N slices, total O(N^2) chars); the index-pair scan is O(N). A 10 s
    bound is unreachable for quadratic at this size yet has a huge
    margin over linear, so it proves the complexity class without being
    wall-clock-flaky on a slow CI runner. A pure nested bomb has only
    deeply-nested spans, so it correctly degrades to the documented
    parse-error fallback (a string) without raising.
    """
    payload = "[" * 60_000 + "]" * 60_000
    start = time.monotonic()
    result = first_json_array(payload)
    assert time.monotonic() - start < 10.0
    assert result is None or isinstance(result, str)


def test_valid_array_before_deep_bomb_is_found_fast() -> None:
    """A real top-level array opens first, so it is returned quickly
    even when a deep nested bomb follows it (linearity + correctness)."""
    payload = '[{"skill_name": "ok"}] ' + "[" * 50_000 + "]" * 50_000
    start = time.monotonic()
    result = first_json_array(payload)
    assert time.monotonic() - start < 10.0
    assert result is not None
    assert json.loads(result) == [{"skill_name": "ok"}]


def test_first_json_array_never_raises_recursion_error() -> None:
    """A pathologically deep array degrades to the documented fallback,
    it does not let RecursionError escape the extractor."""
    deep = "[" * 50_000 + "1" + "]" * 50_000
    # Returns a string (the parse-error fallback) rather than raising.
    out = first_json_array(deep)
    assert out is None or isinstance(out, str)


def test_deep_nesting_surfaces_dispatch_error() -> None:
    """A deep blob reaches the LLM dispatcher as a clean DispatchError,
    never a bare RecursionError that would abort a routing chain."""
    import asyncio
    from collections.abc import AsyncIterator

    from skills.dispatchers.llm import LLMDispatcher
    from skills.errors import DispatchError
    from skills.registry import SkillRegistry
    from skills.types import Skill, SkillManifest

    class _DeepRuntime:
        name = "deep"

        async def run(self, prompt: str, **kwargs: Any) -> Any:
            return "[" * 40_000 + "1" + "]" * 40_000

        def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[Any]:
            raise NotImplementedError

    registry = SkillRegistry()
    registry.add(Skill(manifest=SkillManifest(name="x", description="d"), path=Path("/tmp/x")))
    dispatcher = LLMDispatcher(registry, _DeepRuntime())
    with pytest.raises(DispatchError):
        asyncio.run(dispatcher.dispatch("anything"))
