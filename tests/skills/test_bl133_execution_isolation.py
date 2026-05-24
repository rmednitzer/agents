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


_NON_CONTRACT_EXPORT = """\
# Exports a `contract` symbol that is NOT a harness.contract.Contract
# instance. The in-process loader rejects this; the BL-133 subprocess
# executor must match (PR #56 Copilot review).
contract = "this is a string, not a Contract"
"""


_PRINTS_BEFORE_CRASH = """\
# Prints a single character to stdout before exiting. The subprocess
# executor sees a short header (one byte instead of four) on the read;
# pre-fix this raised struct.error out of `_read_frame`.
import sys
sys.stdout.buffer.write(b'x')
sys.stdout.buffer.flush()
sys.exit(1)
"""


_HANGS_ON_IMPORT = """\
# Blocks forever during import. The subprocess executor's
# `timeout_seconds` cap on `load_metadata` kills the child and raises.
import time
while True:
    time.sleep(60)
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

_ENV_PROBE_CONTRACT = """\
import os
from pydantic import BaseModel
from harness.contract import Contract, Severity, predicate


class _S(BaseModel):
    n: int


@predicate(name="env_isolated", severity=Severity.HARD)
def _env_isolated(s):
    return os.environ.get("AGENTS_PARENT_SECRET") is None


contract = Contract[_S, _S](
    name="bl133_env_probe",
    version="1",
    preconditions=[_env_isolated],
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


# --- PR #56 Copilot review follow-ups ---------------------------------


def test_subprocess_rejects_non_contract_export(tmp_path: Path) -> None:
    """Parity with the in-process loader: a `contract` export that is
    not a `harness.contract.Contract` instance is a manifest error,
    not a runtime error. Pre-fix the subprocess executor accepted
    any non-None export and surfaced the failure later when the
    parent tried to serialise its predicates."""
    skill_dir = _write_skill_bundle(
        tmp_path, "sub-proc-non-contract", contract_py=_NON_CONTRACT_EXPORT
    )
    executor = SubprocessSkillContractExecutor(timeout_seconds=10.0)
    skill = discover_skill(skill_dir, executor=executor)
    with pytest.raises(SkillManifestError, match="not a Contract"):
        skill.contract()


def test_subprocess_short_header_is_executor_error(tmp_path: Path) -> None:
    """A child that prints fewer than 4 bytes before exiting gives
    `_read_frame` a truncated header; pre-fix this raised
    `struct.error` out of the executor. Now it raises a documented
    `SkillContractExecutorError`."""
    skill_dir = _write_skill_bundle(
        tmp_path, "sub-proc-short-header", contract_py=_PRINTS_BEFORE_CRASH
    )
    executor = SubprocessSkillContractExecutor(timeout_seconds=10.0)
    skill = discover_skill(skill_dir, executor=executor)
    # The child prints "x" (1 byte) then exits. `_read_frame` sees a
    # truncated header. Either SkillContractExecutorError (the new
    # boundary) or SkillManifestError (the legacy boundary, used by
    # tests upstream); the property we pin is "no raw struct.error".
    with pytest.raises((SkillContractExecutorError, SkillManifestError)):
        skill.contract()


def test_subprocess_lifecycle_closes_child_on_gc(tmp_path: Path) -> None:
    """The subprocess is bound to the returned Contract's lifecycle
    via `weakref.finalize`; dropping the last reference and forcing
    GC closes the child. Pre-fix each `load` leaked a long-lived
    subprocess until process exit."""
    import gc

    skill_dir = _write_skill_bundle(tmp_path, "sub-proc-lifecycle", contract_py=_SIMPLE_CONTRACT)
    executor = SubprocessSkillContractExecutor(timeout_seconds=10.0)
    skill = discover_skill(skill_dir, executor=executor)
    contract = skill.contract()
    assert contract is not None
    # Reach into the proxy to snapshot the subprocess Popen object,
    # so we can poll it after the Contract is GC'd to confirm
    # finalize ran.
    proxy = contract.preconditions[0]
    evaluator = proxy._evaluator
    proc = evaluator._proc
    assert proc is not None
    assert proc.poll() is None  # alive before GC

    # Drop every Python-side reference and force GC. weakref.finalize
    # runs `evaluator.close()` -> the subprocess is signalled to exit.
    skill._contract = None
    skill._contract_loaded = False
    del contract
    del proxy
    gc.collect()

    # Give the subprocess a moment to exit. close() blocks up to 2s
    # internally, so a quick poll here is sufficient.
    import time as _time

    for _ in range(20):
        if proc.poll() is not None:
            break
        _time.sleep(0.05)
    assert proc.poll() is not None, "subprocess did not exit after Contract GC"


def test_subprocess_predicate_after_child_killed_surfaces_error(tmp_path: Path) -> None:
    """If the subprocess is killed between predicate calls (e.g.,
    rlimit kicked in asynchronously), the next predicate call surfaces
    `SkillContractExecutorError` via the new `poll()` check rather
    than leaking BrokenPipeError."""
    skill_dir = _write_skill_bundle(tmp_path, "sub-proc-killed", contract_py=_SIMPLE_CONTRACT)
    executor = SubprocessSkillContractExecutor(timeout_seconds=10.0)
    skill = discover_skill(skill_dir, executor=executor)
    contract = skill.contract()
    assert contract is not None
    proxy = contract.preconditions[0]
    evaluator = proxy._evaluator
    proc = evaluator._proc
    assert proc is not None
    # First call succeeds normally.
    assert proxy(_IpcState(n=1)) is True
    # Kill the subprocess externally; the next predicate call should
    # see a non-None poll() and raise the documented boundary error.
    proc.kill()
    proc.wait(timeout=2.0)
    with pytest.raises(SkillContractExecutorError, match="already exited"):
        proxy(_IpcState(n=1))


def test_subprocess_load_timeout_surfaces_executor_error(tmp_path: Path) -> None:
    """A contract.py that hangs during import hits the
    `timeout_seconds` cap on `load_metadata`; the parent kills the
    child and raises `SkillContractExecutorError`."""
    skill_dir = _write_skill_bundle(tmp_path, "sub-proc-hang", contract_py=_HANGS_ON_IMPORT)
    executor = SubprocessSkillContractExecutor(timeout_seconds=0.5)
    skill = discover_skill(skill_dir, executor=executor)
    with pytest.raises(SkillContractExecutorError, match="timed out"):
        skill.contract()


def test_subprocess_explicit_close_is_idempotent(tmp_path: Path) -> None:
    """Calling `close()` directly on the evaluator works and is safe
    to repeat. Exercises the explicit-teardown path the lifecycle
    finalize hook also uses."""
    skill_dir = _write_skill_bundle(tmp_path, "sub-proc-close", contract_py=_SIMPLE_CONTRACT)
    executor = SubprocessSkillContractExecutor(timeout_seconds=10.0)
    skill = discover_skill(skill_dir, executor=executor)
    contract = skill.contract()
    assert contract is not None
    proxy = contract.preconditions[0]
    evaluator = proxy._evaluator
    proc = evaluator._proc
    assert proc is not None
    assert proc.poll() is None
    # First close shuts it down.
    evaluator.close()
    assert evaluator._closed is True
    # Repeat is a no-op (no raise).
    evaluator.close()
    # Calling a predicate after close raises the documented boundary.
    with pytest.raises(SkillContractExecutorError, match="closed"):
        proxy(_IpcState(n=1))


def test_subprocess_does_not_inherit_parent_secret_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The subprocess executor must not pass parent secret-bearing
    env vars wholesale to untrusted contract code."""
    monkeypatch.setenv("AGENTS_PARENT_SECRET", "top-secret-value")
    skill_dir = _write_skill_bundle(tmp_path, "sub-proc-env", contract_py=_ENV_PROBE_CONTRACT)
    executor = SubprocessSkillContractExecutor(timeout_seconds=10.0)
    skill = discover_skill(skill_dir, executor=executor)
    contract = skill.contract()
    assert contract is not None
    assert contract.preconditions[0](_IpcState(n=1)) is True


# Quiet ruff F401.
_ = sys
