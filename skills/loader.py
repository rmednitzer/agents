"""Skill loader: parse SKILL.md, discover resources, validate.

Per the Agent Skills spec:
- Directory name must match the manifest name field.
- SKILL.md starts with YAML frontmatter delimited by '---' lines.
- Optional subdirectories: scripts/, references/, assets/.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from skills.errors import SkillLoadError, SkillManifestError
from skills.types import Skill, SkillManifest

if TYPE_CHECKING:
    from harness.contract import Contract

__all__ = [
    "discover_skill",
    "parse_skill_md",
]


def parse_skill_md(path: Path) -> tuple[SkillManifest, str]:
    """Parse a SKILL.md file into (manifest, body).

    Args:
        path: Path to the SKILL.md file.

    Returns:
        Tuple of (parsed SkillManifest, raw markdown body).

    Raises:
        SkillLoadError: File missing or unreadable.
        SkillManifestError: Frontmatter parse or validation failed.
    """
    if not path.is_file():
        raise SkillLoadError(str(path), "SKILL.md not found")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillLoadError(str(path), f"read failed: {exc}") from exc
    except UnicodeDecodeError as exc:
        # A SKILL.md that is not valid UTF-8 (e.g. accidentally saved
        # latin-1, or a binary file mis-named) raises UnicodeDecodeError
        # rather than OSError. Translate to the documented
        # ``SkillLoadError`` boundary so an `install_skill` /
        # `discover_skill` caller sees the same shape as a missing-file
        # or read-failure case (`BL-215`, BL-204 class extension on the
        # loader-input leg).
        raise SkillLoadError(str(path), f"not valid UTF-8: {exc}") from exc

    frontmatter_text, body = _split_frontmatter(text, path)

    try:
        raw = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        raise SkillManifestError(str(path), f"YAML parse: {exc}") from exc
    except RecursionError as exc:
        # PyYAML's safe_load recurses through nested mappings without a
        # depth cap; an adversarial SKILL.md with deeply nested YAML
        # can raise RecursionError (not a YAMLError). Translate so the
        # `install_skill` / `discover_skill` callers see the documented
        # `SkillManifestError` contract instead of an internal Python
        # exception (`BL-204`, BL-173 / BL-191 class extension on the
        # manifest-parse leg).
        raise SkillManifestError(str(path), f"YAML parse exceeded recursion depth: {exc}") from exc

    if not isinstance(raw, dict):
        raise SkillManifestError(
            str(path), f"frontmatter must be a mapping, got {type(raw).__name__}"
        )

    try:
        manifest = SkillManifest.model_validate(raw)
    except ValidationError as exc:
        raise SkillManifestError(str(path), str(exc)) from exc

    return manifest, body


def _read_body_only(path: Path) -> str:
    """Read SKILL.md and return only the body (skip frontmatter).

    Used by Skill.body() for lazy loading. Re-parses the file rather
    than relying on shared state because the Skill caches it.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # Same BL-215 boundary as ``parse_skill_md``: an unreadable
        # UTF-8 body surfaces as ``SkillLoadError`` instead of an
        # internal exception when the Skill caller hits the lazy
        # body load.
        raise SkillLoadError(str(path), f"not valid UTF-8: {exc}") from exc
    _, body = _split_frontmatter(text, path)
    return body


def _split_frontmatter(text: str, path: Path) -> tuple[str, str]:
    """Split a markdown file into (frontmatter_yaml, body).

    The frontmatter must start at line 1 with '---' and end with '---'
    on its own line. The body is everything after.
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        raise SkillManifestError(str(path), "SKILL.md is empty")
    if lines[0].rstrip() != "---":
        raise SkillManifestError(
            str(path),
            "SKILL.md must start with '---' frontmatter delimiter on line 1",
        )

    closing_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            closing_idx = i
            break

    if closing_idx is None:
        raise SkillManifestError(str(path), "closing '---' delimiter not found")

    frontmatter_text = "".join(lines[1:closing_idx])
    body = "".join(lines[closing_idx + 1 :])
    return frontmatter_text, body


def discover_skill(
    skill_dir: Path,
    *,
    allow_contract: bool = True,
    executor: Any | None = None,
) -> Skill:
    """Discover a single skill from its directory.

    Args:
        skill_dir: Directory containing SKILL.md and optional
            scripts/, references/, assets/ subdirectories.
        allow_contract: When False, a present ``contract.py`` is not
            executed; ``Skill.contract()`` raises instead. Defaults to
            True (L1 behaviour: in-tree skills are trusted). Untrusted
            sources should pass False (``install_skill`` does).
        executor: Optional ``SkillContractExecutor`` (`BL-133`,
            ADR 0016). When None, ``Skill.contract()`` uses the in-
            process default (the L1 behaviour: import here, evaluate
            here). When a ``SubprocessSkillContractExecutor`` is
            supplied, both import and predicate evaluation happen in
            a subprocess with ``resource.setrlimit`` caps, giving
            crash + resource isolation on opt-in. Additive: existing
            callers do not change.

    Returns:
        A Skill with eagerly-loaded manifest and discovered resource
        maps; the body is lazy.

    Raises:
        SkillLoadError: Directory or SKILL.md missing.
        SkillManifestError: Frontmatter invalid or name mismatches
            directory name.
    """
    if not skill_dir.is_dir():
        raise SkillLoadError(str(skill_dir), "not a directory")

    manifest, _body = parse_skill_md(skill_dir / "SKILL.md")

    if manifest.name != skill_dir.name:
        raise SkillManifestError(
            str(skill_dir / "SKILL.md"),
            f"name {manifest.name!r} does not match directory name {skill_dir.name!r}",
        )

    references = _discover_resources(skill_dir / "references")
    scripts = _discover_resources(skill_dir / "scripts")
    assets = _discover_resources(skill_dir / "assets")

    contract_file = skill_dir / "contract.py"
    contract_path = contract_file if contract_file.is_file() else None

    return Skill(
        manifest=manifest,
        path=skill_dir,
        references=references,
        scripts=scripts,
        assets=assets,
        contract_path=contract_path,
        _allow_contract=allow_contract,
        _executor=executor,
    )


def _load_skill_contract(skill: Skill) -> Contract[Any, Any] | None:
    """Import a skill's contract.py and return its ``contract`` export.

    Returns None when the skill ships no contract.py. The module is
    loaded from its file path (skills are directories, not importable
    packages) under a synthetic module name.

    Raises:
        SkillManifestError: contract.py exists but cannot be imported or
            does not export a Contract instance.
    """
    from harness.contract import Contract

    if skill.contract_path is None:
        return None
    path = skill.contract_path
    spec = importlib.util.spec_from_file_location(
        f"skills._contract_{skill.name.replace('-', '_')}", path
    )
    if spec is None or spec.loader is None:
        raise SkillManifestError(str(path), "cannot create import spec")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise SkillManifestError(str(path), f"import failed: {exc}") from exc
    contract = getattr(module, "contract", None)
    if contract is None:
        raise SkillManifestError(str(path), "contract.py does not export 'contract'")
    if not isinstance(contract, Contract):
        raise SkillManifestError(
            str(path),
            f"'contract' is not a Contract (got {type(contract).__name__})",
        )
    return contract


def _discover_resources(directory: Path) -> dict[str, Path]:
    """Build a name -> Path map of files in a resource directory.

    Returns an empty dict if the directory doesn't exist.
    """
    if not directory.is_dir():
        return {}
    return {entry.name: entry for entry in directory.iterdir() if entry.is_file()}
