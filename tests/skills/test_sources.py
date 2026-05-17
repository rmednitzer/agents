"""Tests for skill installation sources (BL-054)."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from skills.errors import SkillLoadError
from skills.sources import (
    GitHubSkillSource,
    LocalSkillSource,
    SkillSource,
    install_skill,
)

_SKILL_MD = "---\nname: {n}\ndescription: An installed skill.\n---\nbody\n"


def _bundle(root: Path, name: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(_SKILL_MD.format(n=name), encoding="utf-8")
    (d / "references").mkdir()
    (d / "references" / "R.md").write_text("ref", encoding="utf-8")


def test_local_source_is_a_skillsource(tmp_path: Path) -> None:
    assert isinstance(LocalSkillSource(tmp_path), SkillSource)
    assert isinstance(GitHubSkillSource(), SkillSource)


def test_install_from_local_source(tmp_path: Path) -> None:
    src_root = tmp_path / "registry"
    _bundle(src_root, "greeter")
    dest = tmp_path / "installed"

    skill = install_skill(LocalSkillSource(src_root), "greeter", dest)
    assert skill.name == "greeter"
    assert (dest / "greeter" / "SKILL.md").is_file()
    assert "R.md" in skill.references


def test_local_source_idempotent_replace(tmp_path: Path) -> None:
    src_root = tmp_path / "r"
    _bundle(src_root, "s")
    dest = tmp_path / "d"
    install_skill(LocalSkillSource(src_root), "s", dest)
    # Re-install over the existing directory must not error.
    skill = install_skill(LocalSkillSource(src_root), "s", dest)
    assert skill.name == "s"


def test_missing_skill_raises(tmp_path: Path) -> None:
    with pytest.raises(SkillLoadError):
        install_skill(LocalSkillSource(tmp_path), "absent", tmp_path / "out")


@pytest.mark.parametrize("evil", ["../escape", "a/b", "..", ".", "", "x\x00y"])
def test_local_source_rejects_unsafe_names(tmp_path: Path, evil: str) -> None:
    with pytest.raises(SkillLoadError, match="unsafe"):
        LocalSkillSource(tmp_path).fetch(evil, tmp_path / "out")


def test_github_source_rejects_unsafe_name(tmp_path: Path) -> None:
    with pytest.raises(SkillLoadError, match="unsafe"):
        GitHubSkillSource().fetch("../../etc", tmp_path / "out")


def test_github_source_rejects_tar_path_traversal(tmp_path: Path, monkeypatch: Any) -> None:
    # Member escapes the "<prefix>/<name>/" subtree via '..'.
    archive = _make_tar_gz(
        {
            "skills-main/skills/cool/SKILL.md": _SKILL_MD.format(n="cool").encode(),
            "skills-main/skills/cool/../../../pwned.txt": b"owned",
        }
    )

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self) -> bytes:
            return archive

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    with pytest.raises(SkillLoadError, match=r"unsafe|escapes"):
        install_skill(GitHubSkillSource(path_prefix="skills"), "cool", tmp_path / "o")
    assert not (tmp_path / "pwned.txt").exists()
    assert not (tmp_path.parent / "pwned.txt").exists()


def _make_tar_gz(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in entries.items():
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_github_source_extracts_subtree(tmp_path: Path, monkeypatch: Any) -> None:
    archive = _make_tar_gz(
        {
            "skills-main/skills/cool/SKILL.md": _SKILL_MD.format(n="cool").encode(),
            "skills-main/skills/cool/references/X.md": b"x",
            "skills-main/skills/other/SKILL.md": b"---\nname: other\n---\n",
            "skills-main/README.md": b"ignore",
        }
    )

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self) -> bytes:
            return archive

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    src = GitHubSkillSource(repo="anthropics/skills", ref="main", path_prefix="skills")
    skill = install_skill(src, "cool", tmp_path / "out")
    assert skill.name == "cool"
    assert (tmp_path / "out" / "cool" / "references" / "X.md").read_text() == "x"


def test_github_source_not_found_raises(tmp_path: Path, monkeypatch: Any) -> None:
    archive = _make_tar_gz({"skills-main/README.md": b"x"})

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self) -> bytes:
            return archive

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    with pytest.raises(SkillLoadError, match="not found"):
        install_skill(GitHubSkillSource(path_prefix="skills"), "nope", tmp_path / "o")


class _FakeResp:
    """Response double that also exposes a Content-Length header."""

    def __init__(self, data: bytes, content_length: str | None = None) -> None:
        self._data = data
        self._cl = content_length

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def getheader(self, name: str) -> str | None:
        return self._cl if name == "Content-Length" else None

    def read(self) -> bytes:
        return self._data


def _patch(monkeypatch: Any, data: bytes, content_length: str | None = None) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResp(data, content_length))


def _cool_archive(extra: dict[str, bytes] | None = None) -> bytes:
    entries = {"skills-main/skills/cool/SKILL.md": _SKILL_MD.format(n="cool").encode()}
    if extra:
        entries.update(extra)
    return _make_tar_gz(entries)


def test_github_source_rejects_oversized_download(tmp_path: Path, monkeypatch: Any) -> None:
    _patch(monkeypatch, _cool_archive())
    src = GitHubSkillSource(path_prefix="skills", max_download_bytes=5)
    with pytest.raises(SkillLoadError, match="too large"):
        install_skill(src, "cool", tmp_path / "o")


def test_github_source_rejects_via_content_length(tmp_path: Path, monkeypatch: Any) -> None:
    _patch(monkeypatch, b"unused", content_length="1000000")
    src = GitHubSkillSource(path_prefix="skills", max_download_bytes=100)
    with pytest.raises(SkillLoadError, match="too large"):
        install_skill(src, "cool", tmp_path / "o")


def test_github_source_tolerates_bad_content_length(tmp_path: Path, monkeypatch: Any) -> None:
    _patch(monkeypatch, _cool_archive(), content_length="not-an-int")
    skill = install_skill(GitHubSkillSource(path_prefix="skills"), "cool", tmp_path / "o")
    assert skill.name == "cool"


def test_github_source_rejects_too_many_members(tmp_path: Path, monkeypatch: Any) -> None:
    _patch(monkeypatch, _cool_archive({"skills-main/skills/cool/a.txt": b"a"}))
    src = GitHubSkillSource(path_prefix="skills", max_members=1)
    with pytest.raises(SkillLoadError, match="too many archive members"):
        install_skill(src, "cool", tmp_path / "o")


def test_github_source_rejects_member_too_large(tmp_path: Path, monkeypatch: Any) -> None:
    _patch(monkeypatch, _cool_archive())
    src = GitHubSkillSource(path_prefix="skills", max_file_bytes=4)
    with pytest.raises(SkillLoadError, match="member too large"):
        install_skill(src, "cool", tmp_path / "o")


def test_github_source_rejects_total_too_large(tmp_path: Path, monkeypatch: Any) -> None:
    _patch(monkeypatch, _cool_archive())
    src = GitHubSkillSource(path_prefix="skills", max_file_bytes=10_000, max_total_bytes=4)
    with pytest.raises(SkillLoadError, match="total uncompressed size"):
        install_skill(src, "cool", tmp_path / "o")


def test_github_source_checksum_match_succeeds(tmp_path: Path, monkeypatch: Any) -> None:
    archive = _cool_archive()
    _patch(monkeypatch, archive)
    src = GitHubSkillSource(path_prefix="skills", sha256=hashlib.sha256(archive).hexdigest())
    skill = install_skill(src, "cool", tmp_path / "o")
    assert skill.name == "cool"


def test_github_source_checksum_mismatch_raises(tmp_path: Path, monkeypatch: Any) -> None:
    _patch(monkeypatch, _cool_archive())
    src = GitHubSkillSource(path_prefix="skills", sha256="00" * 32)
    with pytest.raises(SkillLoadError, match="checksum mismatch"):
        install_skill(src, "cool", tmp_path / "o")
