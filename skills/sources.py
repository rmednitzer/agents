"""Skill installation from registries (BL-054, ADR 0007).

A SkillSource fetches a skill bundle into a local directory; the
framework does not bind to one registry. Two concrete sources ship:

- LocalSkillSource: copy from a local tree (filesystem mirrors,
  vendored bundles, tests). No network.
- GitHubSkillSource: download a repo tarball and extract one skill
  subdirectory (the ``anthropics/skills`` layout). Network; the stdlib
  only, no extra dependency.

A marketplace source (Vercel ``skills.sh``) is just another SkillSource
implementation; the Protocol is the extension point. ``install_skill``
fetches, then validates via ``discover_skill`` so a bad bundle fails at
install time, not at dispatch.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import shutil
import tarfile
import urllib.request
from pathlib import Path
from typing import Protocol, runtime_checkable

from skills.errors import SkillLoadError
from skills.loader import discover_skill
from skills.types import Skill

__all__ = [
    "GitHubSkillSource",
    "LocalSkillSource",
    "SkillSource",
    "install_skill",
]


def _safe_name(name: str) -> str:
    """Reject skill names that are not a single, traversal-free path part.

    A skill name is attacker-influenced (it indexes into a registry /
    repo). Anything with a separator, ``..``, a leading dot, or that is
    absolute could escape the source root or install directory.
    """
    if (
        not name
        or name in (".", "..")
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or Path(name).name != name
    ):
        raise SkillLoadError(name, "unsafe skill name (must be a single path component)")
    return name


def _safe_target(base: Path, member_rel: str) -> Path:
    """Resolve an archive member under ``base``, refusing any escape.

    Defends against tar path traversal (``../../etc``) and absolute
    member paths in untrusted/compromised archives.
    """
    if member_rel.startswith(("/", "\\")) or ".." in Path(member_rel).parts:
        raise SkillLoadError(member_rel, "unsafe archive member path")
    resolved = (base / member_rel).resolve()
    if resolved != base.resolve() and base.resolve() not in resolved.parents:
        raise SkillLoadError(member_rel, "archive member escapes install directory")
    return resolved


@runtime_checkable
class SkillSource(Protocol):
    """Materializes a skill bundle directory locally.

    ``fetch`` writes the bundle (SKILL.md + optional resources) into
    ``dest / name`` and returns that directory. Implementations must be
    idempotent: fetching over an existing directory replaces it.
    """

    def fetch(self, name: str, dest: Path) -> Path: ...


class LocalSkillSource:
    """Copies a skill subtree from a local root (no network)."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def fetch(self, name: str, dest: Path) -> Path:
        _safe_name(name)
        src = self._root / name
        if not (src / "SKILL.md").is_file():
            raise SkillLoadError(str(src), "no SKILL.md at source")
        target = dest / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
        return target


class GitHubSkillSource:
    """Fetches a skill from a GitHub repo's tarball (stdlib only).

    ``repo`` is "owner/name"; ``path_prefix`` locates the skills
    directory within the repo (e.g. "" for repo-root skills, "skills"
    for a nested layout). The skill lives at
    ``<path_prefix>/<name>/SKILL.md`` inside the archive.

    Supply-chain note: ``ref`` is resolved as a branch by default
    (``codeload`` heads URL), so the same ``ref`` can return different
    bytes over time (force-push, account takeover). For reproducible,
    tamper-evident installs pass an immutable ``ref`` (a commit SHA or
    release tag) and a ``sha256`` digest of the expected tarball; a
    mismatch fails the install. The extraction caps
    (``max_download_bytes``, ``max_members``, ``max_file_bytes``,
    ``max_total_bytes``) bound a hostile or corrupt archive
    (decompression bomb, member flood) since stdlib ``tarfile`` does
    not. Member paths are still validated against traversal/escape.
    """

    _CODELOAD = "https://codeload.github.com/{repo}/tar.gz/refs/heads/{ref}"

    def __init__(
        self,
        repo: str = "anthropics/skills",
        ref: str = "main",
        path_prefix: str = "",
        *,
        sha256: str | None = None,
        max_download_bytes: int = 64 * 1024 * 1024,
        max_members: int = 10_000,
        max_file_bytes: int = 16 * 1024 * 1024,
        max_total_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        self._repo = repo
        self._ref = ref
        self._prefix = path_prefix.strip("/")
        self._sha256 = sha256.lower() if sha256 is not None else None
        self._max_download_bytes = max_download_bytes
        self._max_members = max_members
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes

    def fetch(self, name: str, dest: Path) -> Path:
        _safe_name(name)
        url = self._CODELOAD.format(repo=self._repo, ref=self._ref)
        data = self._download(url)
        if self._sha256 is not None:
            digest = hashlib.sha256(data).hexdigest()
            if not hmac.compare_digest(digest, self._sha256):
                raise SkillLoadError(url, "archive checksum mismatch (expected sha256 differs)")
        target = (dest / name).resolve()
        if target.exists():
            shutil.rmtree(target)
        wanted = f"{self._prefix}/{name}/".lstrip("/")
        extracted = False
        total = 0
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            # Archive root is "<repo>-<ref>/"; strip it, then match the
            # "<prefix>/<name>/" subtree. Every member path is validated
            # against the install dir before any write (tar traversal),
            # and size/count caps bound a decompression bomb.
            for index, m in enumerate(tar):
                if index >= self._max_members:
                    raise SkillLoadError(url, f"too many archive members (> {self._max_members})")
                rel = m.name.split("/", 1)[1] if "/" in m.name else ""
                if not rel.startswith(wanted) or not m.isfile():
                    continue
                if m.size > self._max_file_bytes:
                    raise SkillLoadError(url, f"archive member too large: {m.name}")
                inner = rel[len(wanted) :]
                out = _safe_target(target, inner)
                out.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(m)
                if src is not None:
                    payload = src.read(self._max_file_bytes + 1)
                    if len(payload) > self._max_file_bytes:
                        raise SkillLoadError(url, f"archive member too large: {m.name}")
                    total += len(payload)
                    if total > self._max_total_bytes:
                        raise SkillLoadError(url, "archive exceeds total uncompressed size budget")
                    out.write_bytes(payload)
                    extracted = True
        if not extracted:
            raise SkillLoadError(url, f"skill {name!r} not found in archive")
        return target

    def _download(self, url: str) -> bytes:
        """Fetch ``url``, rejecting an over-large body before extraction.

        Honours ``Content-Length`` when the response exposes it, and
        always re-checks the materialised length so a lying or absent
        header cannot bypass the cap.
        """
        with urllib.request.urlopen(url, timeout=30) as resp:
            getheader = getattr(resp, "getheader", None)
            if callable(getheader):
                raw = getheader("Content-Length")
                if raw is not None:
                    try:
                        declared = int(raw)
                    except (TypeError, ValueError):
                        declared = -1
                    if declared > self._max_download_bytes:
                        raise SkillLoadError(
                            url,
                            f"archive too large ({declared} bytes > {self._max_download_bytes})",
                        )
            data: bytes = resp.read()
        if len(data) > self._max_download_bytes:
            raise SkillLoadError(
                url, f"archive too large ({len(data)} bytes > {self._max_download_bytes})"
            )
        return data


def install_skill(
    source: SkillSource, name: str, dest: Path, *, allow_contract: bool = False
) -> Skill:
    """Fetch ``name`` via ``source`` into ``dest`` and validate it.

    Returns the discovered Skill. Raises SkillLoadError /
    SkillManifestError if the fetched bundle is missing or invalid, so a
    broken install fails here rather than at dispatch.

    ``allow_contract`` defaults to False: an installed bundle is from an
    untrusted source, so its ``contract.py`` (if any) is not executed by
    ``Skill.contract()``. Pass ``allow_contract=True`` only for a source
    you trust (e.g. a checksum-verified ``GitHubSkillSource`` pinned to
    an immutable ref). This is a deliberate secure default for the
    network boundary; the L1 ``discover_skill`` default is unchanged.
    """
    dest.mkdir(parents=True, exist_ok=True)
    bundle = source.fetch(name, dest)
    return discover_skill(bundle, allow_contract=allow_contract)
