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
    """

    _CODELOAD = "https://codeload.github.com/{repo}/tar.gz/refs/heads/{ref}"

    def __init__(
        self, repo: str = "anthropics/skills", ref: str = "main", path_prefix: str = ""
    ) -> None:
        self._repo = repo
        self._ref = ref
        self._prefix = path_prefix.strip("/")

    def fetch(self, name: str, dest: Path) -> Path:
        url = self._CODELOAD.format(repo=self._repo, ref=self._ref)
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        target = dest / name
        if target.exists():
            shutil.rmtree(target)
        wanted = f"{self._prefix}/{name}/".lstrip("/")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = tar.getmembers()
            # Archive root is "<repo>-<ref>/"; strip it, then match the
            # "<prefix>/<name>/" subtree.
            extracted = False
            for m in members:
                rel = m.name.split("/", 1)[1] if "/" in m.name else ""
                if not rel.startswith(wanted) or not m.isfile():
                    continue
                inner = rel[len(wanted) :]
                out = target / inner
                out.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(m)
                if src is not None:
                    out.write_bytes(src.read())
                    extracted = True
        if not extracted:
            raise SkillLoadError(url, f"skill {name!r} not found in archive")
        return target


def install_skill(source: SkillSource, name: str, dest: Path) -> Skill:
    """Fetch ``name`` via ``source`` into ``dest`` and validate it.

    Returns the discovered Skill. Raises SkillLoadError /
    SkillManifestError if the fetched bundle is missing or invalid, so a
    broken install fails here rather than at dispatch.
    """
    dest.mkdir(parents=True, exist_ok=True)
    bundle = source.fetch(name, dest)
    return discover_skill(bundle)
