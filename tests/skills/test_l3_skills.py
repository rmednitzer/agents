"""L3 skills changes: source hardening, providers, default chain.

Covers audit A7 (LocalSkillSource symlink refusal), BL-161 (GitHub
non-file member rejection; registry allow_contract passthrough;
name@version rpartition), BL-110 (HashingEmbeddingProvider), BL-103
(default_dispatcher), BL-112 (signature verification, marketplace).
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from skills.dispatchers import default_dispatcher
from skills.dispatchers.embedding import EmbeddingDispatcher
from skills.dispatchers.instrumented import InstrumentedDispatcher
from skills.embedding_providers import HashingEmbeddingProvider
from skills.errors import SkillLoadError
from skills.registry import SkillRegistry
from skills.sources import (
    GitHubSkillSource,
    LocalSkillSource,
    MarketplaceSkillSource,
    SkillSource,
    install_skill,
)

_SKILL_MD = "---\nname: {n}\ndescription: An installed L3 skill for routing.\n---\nbody\n"


def _write_skill(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(_SKILL_MD.format(n=name), encoding="utf-8")
    return d


def _make_tar_gz(entries: dict[str, bytes], *, symlinks: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in entries.items():
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for path, link in (symlinks or {}).items():
            info = tarfile.TarInfo(name=path)
            info.type = tarfile.SYMTYPE
            info.linkname = link
            tar.addfile(info)
    return buf.getvalue()


def _patch(monkeypatch: Any, data: bytes) -> None:
    class _Resp:
        def __init__(self) -> None:
            self._d = data

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def getheader(self, name: str) -> str | None:
            return None

        def read(self, amt: int = -1) -> bytes:
            if amt < 0:
                c, self._d = self._d, b""
                return c
            c, self._d = self._d[:amt], self._d[amt:]
            return c

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())


# --- A7: LocalSkillSource symlink refusal -----------------------------


def test_local_source_refuses_symlink_in_subtree(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    skill = _write_skill(root, "cool")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    (skill / "references").mkdir()
    # A crafted mirror points a resource at a host secret.
    (skill / "references" / "leak").symlink_to(secret)
    with pytest.raises(SkillLoadError, match="symlink in skill source"):
        install_skill(LocalSkillSource(root), "cool", tmp_path / "out")
    # The secret's contents were NOT copied into the bundle.
    assert not (tmp_path / "out" / "cool" / "references" / "leak").exists()


def test_local_source_copies_regular_files(tmp_path: Path) -> None:
    root = tmp_path / "r"
    skill = _write_skill(root, "cool")
    (skill / "references").mkdir()
    (skill / "references" / "X.md").write_text("doc")
    s = install_skill(LocalSkillSource(root), "cool", tmp_path / "out")
    assert s.name == "cool"
    assert (tmp_path / "out" / "cool" / "references" / "X.md").read_text() == "doc"


# --- BL-161: GitHub non-file member rejection -------------------------


def test_github_rejects_symlink_member(tmp_path: Path, monkeypatch: Any) -> None:
    archive = _make_tar_gz(
        {"skills-main/skills/cool/SKILL.md": _SKILL_MD.format(n="cool").encode()},
        symlinks={"skills-main/skills/cool/evil": "/etc/passwd"},
    )
    _patch(monkeypatch, archive)
    with pytest.raises(SkillLoadError, match="non-file archive member"):
        install_skill(GitHubSkillSource(path_prefix="skills"), "cool", tmp_path / "o")


def test_github_per_member_clamp_enforced(tmp_path: Path, monkeypatch: Any) -> None:
    big = b"A" * 4096
    archive = _make_tar_gz(
        {
            "skills-main/skills/cool/SKILL.md": _SKILL_MD.format(n="cool").encode(),
            "skills-main/skills/cool/blob.bin": big,
        }
    )
    _patch(monkeypatch, archive)
    src = GitHubSkillSource(path_prefix="skills", max_total_bytes=100)
    with pytest.raises(SkillLoadError, match="total uncompressed size"):
        install_skill(src, "cool", tmp_path / "o")


# --- BL-161: registry allow_contract passthrough ----------------------


def test_registry_from_directory_allow_contract_false(tmp_path: Path) -> None:
    d = _write_skill(tmp_path, "withc")
    (d / "contract.py").write_text("contract = object()\n")
    reg = SkillRegistry.from_directory(tmp_path, allow_contract=False)
    skill = reg.get("withc")
    assert skill is not None
    from skills.errors import SkillManifestError

    with pytest.raises(SkillManifestError, match="contract execution disabled"):
        skill.contract()


# --- BL-161: name@version via rpartition ------------------------------


def test_registry_name_with_at_resolves_on_last_at(tmp_path: Path) -> None:
    from skills.types import Skill, SkillManifest

    reg = SkillRegistry()
    # A skill whose name itself contains '@'.
    m = SkillManifest(name="a", description="d", metadata={"version": "2.0"})
    sk = Skill(manifest=m, path=tmp_path)
    reg.add(sk)
    # rpartition('@') splits on the LAST '@': base 'a', version '2.0'.
    assert reg.get("a@2.0") is sk
    assert reg.get("a@9.9") is None


# --- BL-110: HashingEmbeddingProvider ---------------------------------


@pytest.mark.asyncio
async def test_hashing_provider_deterministic_and_normed() -> None:
    p = HashingEmbeddingProvider(dim=64)
    a1, b1 = await p.embed(["hello world", "totally different text"])
    (a2,) = await p.embed(["hello world"])
    assert a1 == a2  # deterministic
    assert a1 != b1
    assert len(a1) == 64
    norm = sum(x * x for x in a1) ** 0.5
    assert abs(norm - 1.0) < 1e-9


@pytest.mark.asyncio
async def test_embedding_dispatcher_usable_with_hashing_provider() -> None:
    from skills.types import Skill, SkillManifest

    reg = SkillRegistry()
    reg.add(
        Skill(
            manifest=SkillManifest(name="weather", description="forecast rain sun wind"),
            path=Path("."),
        )
    )
    reg.add(
        Skill(
            manifest=SkillManifest(name="finance", description="stocks bonds market"),
            path=Path("."),
        )
    )
    disp = EmbeddingDispatcher(reg, HashingEmbeddingProvider())
    matches = await disp.dispatch("will it rain today", limit=1)
    assert matches
    assert matches[0].skill_name == "weather"


# --- BL-103: default_dispatcher ---------------------------------------


@pytest.mark.asyncio
async def test_default_dispatcher_is_instrumented_and_model_free() -> None:
    from skills.types import Skill, SkillManifest

    reg = SkillRegistry()
    reg.add(
        Skill(
            manifest=SkillManifest(
                name="deploy",
                description="ship code",
                metadata={"triggers": "deploy,release"},
            ),
            path=Path("."),
        )
    )
    disp = default_dispatcher(reg)
    assert isinstance(disp, InstrumentedDispatcher)
    matches = await disp.dispatch("please deploy the service", limit=1)
    assert matches
    assert matches[0].skill_name == "deploy"
    assert disp.stats.calls == 1


# --- BL-112: signature verification + marketplace ---------------------


def test_github_signature_verifier_rejects_bad_signature(tmp_path: Path, monkeypatch: Any) -> None:
    archive = _make_tar_gz(
        {"skills-main/skills/cool/SKILL.md": _SKILL_MD.format(n="cool").encode()}
    )
    _patch(monkeypatch, archive)
    src = GitHubSkillSource(
        path_prefix="skills",
        signature=b"bad",
        verify_signature=lambda data, sig: sig == b"good",
    )
    with pytest.raises(SkillLoadError, match="signature verification failed"):
        install_skill(src, "cool", tmp_path / "o")


def test_github_signature_verifier_accepts_good_signature(tmp_path: Path, monkeypatch: Any) -> None:
    archive = _make_tar_gz(
        {"skills-main/skills/cool/SKILL.md": _SKILL_MD.format(n="cool").encode()}
    )
    _patch(monkeypatch, archive)
    src = GitHubSkillSource(
        path_prefix="skills",
        signature=b"good",
        verify_signature=lambda data, sig: sig == b"good",
    )
    skill = install_skill(src, "cool", tmp_path / "o")
    assert skill.name == "cool"


def test_marketplace_source_is_a_skillsource() -> None:
    src = MarketplaceSkillSource("https://skills.example/{name}.tar.gz")
    assert isinstance(src, SkillSource)


def test_marketplace_rejects_non_http_template() -> None:
    with pytest.raises(ValueError, match="http"):
        MarketplaceSkillSource("ftp://nope/{name}.tgz")


def test_marketplace_extracts_flat_tarball(tmp_path: Path, monkeypatch: Any) -> None:
    # Flat layout: members are "<name>/..." (strip_components=0).
    archive = _make_tar_gz({"cool/SKILL.md": _SKILL_MD.format(n="cool").encode()})
    _patch(monkeypatch, archive)
    src = MarketplaceSkillSource("https://skills.example/{name}.tar.gz")
    skill = install_skill(src, "cool", tmp_path / "o")
    assert skill.name == "cool"


def test_local_source_preserves_executable_bit(tmp_path: Path) -> None:
    """A shipped executable script keeps its execute bit (Codex P2)."""
    import os
    import stat

    root = tmp_path / "r"
    skill = _write_skill(root, "tool")
    (skill / "scripts").mkdir()
    script = skill / "scripts" / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    install_skill(LocalSkillSource(root), "tool", tmp_path / "out")
    out = tmp_path / "out" / "tool" / "scripts" / "run.sh"
    assert out.is_file()
    assert stat.S_IMODE(os.stat(out).st_mode) & 0o111  # execute bit kept
