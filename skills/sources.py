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
import os
import shutil
import stat
import tarfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from skills.errors import SkillLoadError
from skills.loader import discover_skill
from skills.types import Skill

__all__ = [
    "GitHubSkillSource",
    "LocalSkillSource",
    "MarketplaceSkillSource",
    "SignatureVerifier",
    "SkillSource",
    "install_skill",
]

# (archive_bytes, signature_bytes) -> bool. Returning False fails the
# install. The framework binds no signature library (mirrors ADR 0001 /
# ADR 0006: the framework does not pick a vendor); a caller supplies an
# ed25519 / sigstore / minisign verifier and imports its crypto lazily.
SignatureVerifier = Callable[[bytes, bytes], bool]

# Streaming read size for the bounded archive download.
_DOWNLOAD_CHUNK = 64 * 1024


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


def _download_bounded(url: str, cap: int) -> bytes:
    """Fetch ``url`` with a hard cap on bytes pulled into memory.

    ``Content-Length`` is rejected up front when present and over the
    cap. The body is then read in bounded chunks and the read stops the
    moment the cap is exceeded, so a missing or lying header cannot make
    the installer buffer an unbounded response.
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
                if declared > cap:
                    raise SkillLoadError(url, f"archive too large ({declared} bytes > {cap})")
        buf = bytearray()
        while True:
            chunk = resp.read(_DOWNLOAD_CHUNK)
            if not chunk:
                break
            buf += chunk
            if len(buf) > cap:
                raise SkillLoadError(url, f"archive too large (> {cap} bytes)")
    return bytes(buf)


def _extract_subdir(
    data: bytes,
    *,
    url: str,
    wanted: str,
    strip_components: int,
    target: Path,
    max_members: int,
    max_file_bytes: int,
    max_total_bytes: int,
) -> bool:
    """Extract the ``wanted`` subtree of a .tar.gz into ``target``.

    Shared, hardened extraction for every network SkillSource (one
    audited path, not one per source). ``strip_components`` drops that
    many leading path components from each member name before matching
    ``wanted`` (GitHub codeload wraps everything in a single
    ``<repo>-<ref>/`` root: strip 1; a flat marketplace tarball: strip
    0). Member paths are validated against the install dir (traversal),
    non-file members inside the subtree are rejected, and size/count
    caps bound a decompression bomb. Returns True iff at least one file
    was written.
    """
    extracted = False
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for index, m in enumerate(tar):
            if index >= max_members:
                raise SkillLoadError(url, f"too many archive members (> {max_members})")
            parts = m.name.split("/")
            rel = "/".join(parts[strip_components:]) if len(parts) > strip_components else ""
            if not rel.startswith(wanted):
                continue
            if m.isdir():
                continue
            # A non-file member INSIDE the wanted subtree (symlink,
            # hardlink, device, fifo) is rejected, not silently skipped:
            # skipping let a crafted bundle ship a symlink that shadowed
            # a real file so the installed skill differed from the
            # archive with no error (BL-161).
            if not m.isfile():
                raise SkillLoadError(url, f"unsafe non-file archive member: {m.name}")
            inner = rel[len(wanted) :]
            if not inner:
                # Member path equals the skill dir itself: nothing to
                # write under it (writing the dir path as a file would
                # raise IsADirectoryError mid-extraction).
                continue
            if m.size > max_file_bytes:
                raise SkillLoadError(url, f"archive member too large: {m.name}")
            out = _safe_target(target, inner)
            out.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            if src is not None:
                # Clamp each read to the smaller of the per-member cap
                # and the remaining total budget, so the total ceiling
                # cannot be overshot by one large final member before
                # the post-read check (BL-161).
                remaining = max_total_bytes - total
                limit = min(max_file_bytes, max(remaining, 0))
                payload = src.read(limit + 1)
                if len(payload) > max_file_bytes:
                    raise SkillLoadError(url, f"archive member too large: {m.name}")
                total += len(payload)
                if total > max_total_bytes:
                    raise SkillLoadError(url, "archive exceeds total uncompressed size budget")
                out.write_bytes(payload)
                extracted = True
    return extracted


def _prepare_install_dir(dest: Path, name: str) -> Path:
    """Clear and resolve ``dest / name`` without following a symlink.

    A pre-existing ``dest/<name>`` *symlink* is the network-source twin
    of the ``LocalSkillSource`` symlink hole (audit, this wave). The old
    code did ``(dest / name).resolve()`` *before* clearing it: ``resolve``
    follows the link, so ``shutil.rmtree`` then deleted the link's
    target's contents and extraction wrote members fully outside
    ``dest`` (``_safe_target`` only re-validates against the already
    escaped base). Unlink the link itself first (never traverse it),
    then resolve, then assert the result is still under ``dest`` so a
    crafted bundle cannot escape the install directory.
    """
    raw = dest / name
    if raw.is_symlink():
        raw.unlink()
    elif raw.exists():
        shutil.rmtree(raw)
    target = raw.resolve()
    dest_resolved = dest.resolve()
    if target != dest_resolved / name or dest_resolved not in target.parents:
        raise SkillLoadError(str(raw), "install path escapes the destination directory")
    return target


def _verify_integrity(
    data: bytes,
    url: str,
    *,
    sha256: str | None,
    signature: bytes | None,
    verify_signature: SignatureVerifier | None,
) -> None:
    """Optional checksum then optional signature, before extraction."""
    if sha256 is not None:
        digest = hashlib.sha256(data).hexdigest()
        if not hmac.compare_digest(digest, sha256):
            raise SkillLoadError(url, "archive checksum mismatch (expected sha256 differs)")
    if verify_signature is not None:
        if signature is None:
            raise SkillLoadError(url, "signature verifier set but no signature supplied")
        if not verify_signature(data, signature):
            raise SkillLoadError(url, "archive signature verification failed")


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
        # Copy regular files only. The default shutil.copytree
        # dereferences symlinks, so a crafted local mirror with
        # ``references/x -> ~/.ssh/id_rsa`` would copy the secret's
        # CONTENTS into the installed bundle (audit A7). A symlink
        # anywhere in the subtree is refused, not silently followed.
        src_root = src.resolve()
        for path in sorted(src.rglob("*")):
            if path.is_symlink():
                raise SkillLoadError(str(path), "symlink in skill source (refused)")
            if path.is_dir():
                continue
            if not path.is_file():
                raise SkillLoadError(str(path), "non-regular file in skill source (refused)")
            rel = path.resolve().relative_to(src_root)
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(path.read_bytes())
            # Preserve the source permission bits: a skill that ships an
            # executable asset (scripts/*.sh invoked directly) must keep
            # its execute bit, which write_bytes (umask default) drops.
            # The symlink check above already ran, so stat() here reads
            # a confirmed regular file's own mode.
            os.chmod(out, stat.S_IMODE(path.stat().st_mode))
        return target


class GitHubSkillSource:
    """Fetches a skill from a GitHub repo's tarball (stdlib only).

    ``repo`` is "owner/name"; ``path_prefix`` locates the skills
    directory within the repo (e.g. "" for repo-root skills, "skills"
    for a nested layout). The skill lives at
    ``<path_prefix>/<name>/SKILL.md`` inside the archive.

    Supply-chain note: ``ref`` may be a branch (the default, ``main``,
    which is mutable: the same ``ref`` can return different bytes over
    time via force-push or account takeover), a release tag, or a commit
    SHA. The generic ``codeload`` archive path resolves all three. For
    reproducible, tamper-evident installs pass an immutable ``ref`` (a
    commit SHA or release tag) and a ``sha256`` digest of the expected
    tarball; a mismatch fails the install. The extraction caps
    (``max_download_bytes``, ``max_members``, ``max_file_bytes``,
    ``max_total_bytes``) bound a hostile or corrupt archive
    (decompression bomb, member flood) since stdlib ``tarfile`` does
    not. Member paths are still validated against traversal/escape.
    """

    _CODELOAD = "https://codeload.github.com/{repo}/tar.gz/{ref}"

    def __init__(
        self,
        repo: str = "anthropics/skills",
        ref: str = "main",
        path_prefix: str = "",
        *,
        sha256: str | None = None,
        signature: bytes | None = None,
        verify_signature: SignatureVerifier | None = None,
        max_download_bytes: int = 64 * 1024 * 1024,
        max_members: int = 10_000,
        max_file_bytes: int = 16 * 1024 * 1024,
        max_total_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        self._repo = repo
        self._ref = ref
        self._prefix = path_prefix.strip("/")
        self._sha256 = sha256.lower() if sha256 is not None else None
        self._signature = signature
        self._verify_signature = verify_signature
        self._max_download_bytes = max_download_bytes
        self._max_members = max_members
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes

    def fetch(self, name: str, dest: Path) -> Path:
        _safe_name(name)
        url = self._CODELOAD.format(repo=self._repo, ref=self._ref)
        data = _download_bounded(url, self._max_download_bytes)
        _verify_integrity(
            data,
            url,
            sha256=self._sha256,
            signature=self._signature,
            verify_signature=self._verify_signature,
        )
        target = _prepare_install_dir(dest, name)
        # GitHub codeload wraps everything in one "<repo>-<ref>/" root
        # component: strip 1, then match the "<prefix>/<name>/" subtree.
        wanted = f"{self._prefix}/{name}/".lstrip("/")
        extracted = _extract_subdir(
            data,
            url=url,
            wanted=wanted,
            strip_components=1,
            target=target,
            max_members=self._max_members,
            max_file_bytes=self._max_file_bytes,
            max_total_bytes=self._max_total_bytes,
        )
        if not extracted:
            raise SkillLoadError(url, f"skill {name!r} not found in archive")
        return target


class MarketplaceSkillSource:
    """Fetches a skill from a marketplace tarball over HTTP(S) (BL-112).

    A marketplace (e.g. Vercel ``skills.sh``) is just another
    SkillSource: the Protocol is the extension point. ``url_template``
    is formatted with ``name`` (and ``ref`` if the marketplace pins
    versions) to locate a ``.tar.gz``. ``strip_components`` and
    ``path_prefix`` adapt to the marketplace's archive layout
    (``strip_components=0`` for a flat ``<name>/...`` tarball). The same
    hardened bounded download/extraction as ``GitHubSkillSource`` is
    reused, plus the optional ``sha256`` and ``verify_signature`` /
    ``signature`` integrity checks. As with any network source, prefer
    a marketplace that pins immutable, signed artifacts and keep
    ``install_skill(..., allow_contract=False)``.
    """

    def __init__(
        self,
        url_template: str,
        *,
        ref: str = "latest",
        path_prefix: str = "",
        strip_components: int = 0,
        sha256: str | None = None,
        signature: bytes | None = None,
        verify_signature: SignatureVerifier | None = None,
        max_download_bytes: int = 64 * 1024 * 1024,
        max_members: int = 10_000,
        max_file_bytes: int = 16 * 1024 * 1024,
        max_total_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        if not url_template.startswith(("http://", "https://")):
            raise ValueError("url_template must be an http(s) URL")
        self._url_template = url_template
        self._ref = ref
        self._prefix = path_prefix.strip("/")
        self._strip = strip_components
        self._sha256 = sha256.lower() if sha256 is not None else None
        self._signature = signature
        self._verify_signature = verify_signature
        self._max_download_bytes = max_download_bytes
        self._max_members = max_members
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes

    def fetch(self, name: str, dest: Path) -> Path:
        _safe_name(name)
        url = self._url_template.format(name=name, ref=self._ref)
        if not url.startswith(("http://", "https://")):
            raise SkillLoadError(url, "resolved url is not http(s)")
        data = _download_bounded(url, self._max_download_bytes)
        _verify_integrity(
            data,
            url,
            sha256=self._sha256,
            signature=self._signature,
            verify_signature=self._verify_signature,
        )
        target = _prepare_install_dir(dest, name)
        wanted = f"{self._prefix}/{name}/".lstrip("/")
        extracted = _extract_subdir(
            data,
            url=url,
            wanted=wanted,
            strip_components=self._strip,
            target=target,
            max_members=self._max_members,
            max_file_bytes=self._max_file_bytes,
            max_total_bytes=self._max_total_bytes,
        )
        if not extracted:
            raise SkillLoadError(url, f"skill {name!r} not found in archive")
        return target


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
