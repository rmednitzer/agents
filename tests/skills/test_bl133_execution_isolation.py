"""Regression tests for `BL-133` skill contract execution isolation
(ADR 0016).

The Protocol is additive (ADR 0007): every existing caller that does
not pass ``executor=`` continues to use the in-process default and
behaves exactly as before. These tests pin:

- The Protocol satisfies the runtime ``isinstance`` check for both
  reference implementations.
- ``InProcessSkillContractExecutor`` reproduces the legacy
  ``_load_skill_contract`` behaviour (including the "no contract"
  None return).
- ``SubprocessSkillContractExecutor`` loads the contract in a child
  process, returns proxy predicates whose ``__call__`` delegates over
  IPC, and survives a contract that crashes the child (the
  ``SkillContractExecutorError`` boundary).
- ``Skill.contract()`` honours the injected executor, defaulting to
  None (in-process) for backward compatibility.
- ``discover_skill(executor=...)`` and ``install_skill(executor=...)``
  forward the executor to the constructed Skill.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

from skills.errors import SkillManifestError
from skills.execution import (
    InProcessSkillContractExecutor,
    SkillContractExecutor,
    SkillContractExecutorError,
    SubprocessSkillContractExecutor,
)
from skills.loader import discover_skill

# Module-scope pickleable dataclasses (a local-scope class cannot be
# pickled, so the IPC parent->child pickle path would fail).


@dataclasses.dataclass
class _IpcState:
    n: int


# --- Test fixtures: tiny on-disk skill bundles ----------------------


def _write_skill_bundle(
    root: Path,
    name: str,
    *,
    contract_py: str | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A test skill for BL-133.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    if contract_py is not None:
        (skill_dir / "contract.py").write_text(contract_py, encoding="utf-8")
    return skill_dir


_SIMPLE_CONTRACT = """\
from pydantic import BaseModel
from harness.contract import Contract, Severity, predicate


class _In(BaseModel):
    n: int


class _Out(BaseModel):
    n: int


@predicate(name="positive_input", severity=Severity.HARD)
def _positive_input(s):
    return s.n > 0


@predicate(name="non_negative_output", severity=Severity.SOFT)
def _non_negative_output(s):
    return s.n >= 0


contract = Contract[_In, _Out](
    name="bl133_simple",
    version="1",
    preconditions=[_positive_input],
    postconditions=[_non_negative_output],
)
"""


_CRASHING_CONTRACT = """\
import sys
sys.exit(7)
"""


_MALFORMED_CONTRACT = """\
this is not valid python syntax !!!
"""


_NO_EXPORT_CONTRACT = """\
# A contract.py that imports cleanly but does NOT export `contract`.
x = 1
"""


_RAISING_PREDICATE_CONTRACT = """\
from pydantic import BaseModel
from harness.contract import Contract, Severity, predicate


class _S(BaseModel):
    n: int


@predicate(name="raiser", severity=Severity.HARD)
def _raiser(s):
    raise RuntimeError("simulated predicate failure")


contract = Contract[_S, _S](
    name="bl133_raiser",
    version="1",
    preconditions=[_raiser],
    postconditions=[],
)
"""


# --- Protocol smoke test ---------------------------------------------


def test_both_executors_satisfy_protocol() -> None:
    """Both reference impls satisfy the runtime ``isinstance`` check."""
    assert isinstance(InProcessSkillContractExecutor(), SkillContractExecutor)
    assert isinstance(SubprocessSkillContractExecutor(), SkillContractExecutor)


# --- InProcess executor ----------------------------------------------


def test_in_process_returns_none_when_no_contract_py(tmp_path: Path) -> None:
    """No ``contract.py`` -> ``load`` returns None (parity with the
    legacy ``_load_skill_contract``)."""
    skill_dir = _write_skill_bundle(tmp_path, "no-contract")
    skill = discover_skill(skill_dir, executor=InProcessSkillContractExecutor())
    assert skill.contract() is None


def test_in_process_loads_contract_module(tmp_path: Path) -> None:
    """``InProcessSkillContractExecutor`` produces a working contract
    whose predicates run in-process and return real bools."""
    skill_dir = _write_skill_bundle(tmp_path, "in-proc-simple", contract_py=_SIMPLE_CONTRACT)
    skill = discover_skill(skill_dir, executor=InProcessSkillContractExecutor())
    contract = skill.contract()
    assert contract is not None
    assert contract.name == "bl133_simple"
    assert [p.name for p in contract.preconditions] == ["positive_input"]
    assert [p.name for p in contract.postconditions] == ["non_negative_output"]
    # Real callable from the in-tree module: evaluate it directly.
    # Use the contract.py's own _In model via a synthesized object
    # the predicate's `n` attribute access works on.

    class _Obj:
        def __init__(self, n: int) -> None:
            self.n = n

    pre = contract.preconditions[0]
    assert pre(_Obj(3)) is True
    assert pre(_Obj(-1)) is False


def test_default_skill_uses_in_process(tmp_path: Path) -> None:
    """A Skill discovered without an explicit executor falls back to
    the L1 in-process path. Backward compatibility regression guard."""
    skill_dir = _write_skill_bundle(tmp_path, "in-proc-default", contract_py=_SIMPLE_CONTRACT)
    skill = discover_skill(skill_dir)
    contract = skill.contract()
    assert contract is not None
    assert contract.name == "bl133_simple"


# --- Subprocess executor ---------------------------------------------
#
# These tests spawn a real Python subprocess. ``sys.executable`` is the
# active venv interpreter; the child imports from the same venv.


def test_subprocess_loads_contract_module(tmp_path: Path) -> None:
    """The subprocess executor returns proxy predicates whose
    ``__call__`` round-trips a state through IPC and returns the
    predicate's bool. The Contract's name / version metadata
    propagates from the child."""
    skill_dir = _write_skill_bundle(tmp_path, "sub-proc-simple", contract_py=_SIMPLE_CONTRACT)
    executor = SubprocessSkillContractExecutor(cpu_seconds=10, memory_mb=256, timeout_seconds=15.0)
    skill = discover_skill(skill_dir, executor=executor)
    contract = skill.contract()
    assert contract is not None
    assert contract.name == "bl133_simple"
    assert [p.name for p in contract.preconditions] == ["positive_input"]
    assert [p.name for p in contract.postconditions] == ["non_negative_output"]

    # Round-trip the state via pickle (the IPC's parent->child format).
    # The child reconstructs the object; we use a module-scope
    # pickleable dataclass so we do not have to share the contract.py
    # module class across the test boundary.
    pre = contract.preconditions[0]
    assert pre(_IpcState(n=3)) is True
    assert pre(_IpcState(n=-1)) is False


def test_subprocess_returns_none_when_no_contract_py(tmp_path: Path) -> None:
    """No ``contract.py`` -> ``SubprocessSkillContractExecutor.load``
    returns None without spawning a subprocess (no work to do)."""
    skill_dir = _write_skill_bundle(tmp_path, "sub-proc-none")
    skill = discover_skill(skill_dir, executor=SubprocessSkillContractExecutor())
    assert skill.contract() is None


def test_subprocess_translates_import_failure_to_manifest_error(
    tmp_path: Path,
) -> None:
    """A malformed contract.py raises ``SkillManifestError`` in the
    parent (preserves the documented L1 contract); the parent does
    not crash even though the child failed to import."""
    skill_dir = _write_skill_bundle(tmp_path, "sub-proc-malformed", contract_py=_MALFORMED_CONTRACT)
    executor = SubprocessSkillContractExecutor(timeout_seconds=10.0)
    skill = discover_skill(skill_dir, executor=executor)
    with pytest.raises(SkillManifestError):
        skill.contract()


def test_subprocess_translates_missing_export_to_manifest_error(
    tmp_path: Path,
) -> None:
    """A contract.py that imports cleanly but does not export
    ``contract`` raises ``SkillManifestError`` (parity with the
    L1 contract)."""
    skill_dir = _write_skill_bundle(tmp_path, "sub-proc-no-export", contract_py=_NO_EXPORT_CONTRACT)
    executor = SubprocessSkillContractExecutor(timeout_seconds=10.0)
    skill = discover_skill(skill_dir, executor=executor)
    with pytest.raises(SkillManifestError):
        skill.contract()


def test_subprocess_predicate_exception_surfaces_as_executor_error(
    tmp_path: Path,
) -> None:
    """A predicate that raises inside the subprocess surfaces as
    ``SkillContractExecutorError`` in the parent; the harness can
    distinguish "isolation layer caught an exception" from "predicate
    cleanly returned False"."""
    skill_dir = _write_skill_bundle(
        tmp_path, "sub-proc-raise", contract_py=_RAISING_PREDICATE_CONTRACT
    )
    executor = SubprocessSkillContractExecutor(timeout_seconds=10.0)
    skill = discover_skill(skill_dir, executor=executor)
    contract = skill.contract()
    assert contract is not None
    with pytest.raises(SkillContractExecutorError, match="raiser"):
        contract.preconditions[0](_IpcState(n=0))


def test_subprocess_load_crash_surfaces_as_manifest_error(tmp_path: Path) -> None:
    """A contract.py that ``sys.exit``s before serving metadata
    surfaces as ``SkillManifestError`` (the import failed) rather
    than killing the harness or hanging the test."""
    skill_dir = _write_skill_bundle(tmp_path, "sub-proc-crash", contract_py=_CRASHING_CONTRACT)
    executor = SubprocessSkillContractExecutor(timeout_seconds=10.0)
    skill = discover_skill(skill_dir, executor=executor)
    # The child sys.exit(7) before writing the metadata frame; the
    # parent detects "no output" and raises an executor-layer error,
    # but the load path documents this as a manifest-load failure.
    with pytest.raises((SkillContractExecutorError, SkillManifestError)):
        skill.contract()


# --- Loader-level forwarding ----------------------------------------


def test_discover_skill_forwards_executor(tmp_path: Path) -> None:
    """``discover_skill(executor=...)`` sets the Skill's executor; the
    instance's ``contract()`` then routes through it."""
    skill_dir = _write_skill_bundle(tmp_path, "disc-forward", contract_py=_SIMPLE_CONTRACT)
    executor = InProcessSkillContractExecutor()
    skill = discover_skill(skill_dir, executor=executor)
    assert skill._executor is executor


def test_install_skill_forwards_executor(tmp_path: Path) -> None:
    """``install_skill(executor=...)`` forwards through the same
    ``discover_skill`` path."""
    from skills.sources import LocalSkillSource, install_skill

    # Build a "source" directory and an "install" target.
    source_root = tmp_path / "src"
    source_root.mkdir()
    bundle_root = _write_skill_bundle(source_root, "inst-forward", contract_py=_SIMPLE_CONTRACT)
    dest = tmp_path / "installed"
    src = LocalSkillSource(source_root)
    executor = InProcessSkillContractExecutor()
    skill = install_skill(
        src,
        bundle_root.name,
        dest,
        allow_contract=True,
        executor=executor,
    )
    assert skill._executor is executor


# Quiet ruff F401.
_ = sys
