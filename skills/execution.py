"""Skill contract execution isolation (BL-133, ADR 0016).

A skill bundle may ship a ``contract.py`` exporting ``contract: Contract``;
``Skill.contract()`` returns it for composition with the workload contract
(BL-052). Loading and evaluating predicates from an untrusted bundle is
arbitrary-Python execution, so the original implementation refused
``contract.py`` outright when the skill came from an untrusted source
(``install_skill`` defaults to ``allow_contract=False``, see L3).

This module introduces the ``SkillContractExecutor`` Protocol so an
operator can choose *how* to run an opted-in contract:

- ``InProcessSkillContractExecutor`` (default, backward-compatible): the
  L1 behaviour. ``importlib`` loads the module in the parent process and
  every predicate evaluates in the parent's interpreter. Trust required:
  full.

- ``SubprocessSkillContractExecutor``: load and evaluate every predicate
  in a long-lived Python subprocess, with ``resource.setrlimit`` (POSIX)
  bounding CPU time, address-space, and open files. Crash isolation is
  real (a contract that segfaults or stack-overflows does not kill the
  harness); resource exhaustion is bounded; the subprocess inherits the
  parent's filesystem and network unless the operator restricts that
  out-of-band (container, seccomp, mount namespace). Trust required:
  reduced from "full" to "trust to read the filesystem and use the
  network, but not to crash or starve the harness".

Container / seccomp / namespace-based isolation is intentionally
out-of-tree (ADR 0001 no-vendor-binding stance, the same pattern as
``KeyProvider`` / ``Embedder`` / ``SkillSource``): the Protocol is the
in-tree extension point, and a deployment that needs true
capability-isolation supplies a custom executor.

The Protocol is additive (ADR 0007): the existing call sites that do
not pass ``executor=`` continue to use the in-process default, so no
present caller changes behaviour. ``LIMITATIONS.md`` L3 is updated to
reflect the new tier.
"""

from __future__ import annotations

import json
import os
import pickle
import struct
import subprocess
import sys
import threading
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from skills.errors import SkillManifestError

if TYPE_CHECKING:
    from harness.contract import Contract, Severity
    from skills.types import Skill

__all__ = [
    "InProcessSkillContractExecutor",
    "SkillContractExecutor",
    "SkillContractExecutorError",
    "SubprocessSkillContractExecutor",
]


class SkillContractExecutorError(RuntimeError):
    """Raised when an executor cannot load or evaluate a contract.

    Distinct from ``SkillManifestError`` (which is the documented
    contract for malformed manifests / unparseable contract.py): an
    executor-level failure (subprocess crashed, IPC framing broke,
    resource budget exhausted before load completed) is wrapped in
    this type so the caller can distinguish "this contract is bad"
    from "the isolation layer broke".
    """


@runtime_checkable
class SkillContractExecutor(Protocol):
    """How a Skill bundle's ``contract.py`` is loaded and evaluated.

    Implementations:
    - Return None when ``skill.contract_path`` is None (no
      contract.py to load).
    - Raise ``SkillManifestError`` for an unparseable / mis-shaped
      contract.py (matching the documented L1 contract).
    - Raise ``SkillContractExecutorError`` for an isolation-layer
      failure that is not the contract's fault.
    - Return a ``Contract`` (real or proxy) on success. The
      returned object is interoperable with ``harness.compose_contracts``
      and with the Predicate Protocol; the harness does not need to
      know whether the predicates run in-process or via IPC.
    """

    def load(self, skill: Skill) -> Contract[Any, Any] | None: ...


class InProcessSkillContractExecutor:
    """The L1 default: load and evaluate the contract in this process.

    Backward compatible: a Skill constructed without an explicit
    executor uses this one and behaves exactly as before. Trust
    required: full (arbitrary Python from the bundle runs here).
    """

    name: str = "in-process"

    def load(self, skill: Skill) -> Contract[Any, Any] | None:
        # Delegate to the long-standing loader so the L1 path is one
        # implementation, not two. ``_load_skill_contract`` returns
        # None when the bundle has no contract.py.
        from skills.loader import _load_skill_contract

        return _load_skill_contract(skill)


# --- Subprocess executor ---------------------------------------------
#
# IPC: length-prefixed framing on the child's stdin (parent->child) and
# stdout (child->parent). Each frame is a 4-byte big-endian length
# followed by that many bytes. Parent->child frames are pickled
# (parent owns the source; the child loading malicious bytes is no
# worse than the contract.py the child already imports). Child->parent
# frames are JSON: a bool / string / structured error, never arbitrary
# pickled objects, so a malicious contract.py cannot RCE the parent
# via the IPC.


_FRAME_LEN = struct.Struct(">I")
# Upper bound on a single IPC frame body, applied on the parent side
# (`BL-216`): the 4-byte big-endian length prefix can encode up to
# ~4 GiB, but a legitimate frame (a small JSON metadata blob or an
# {"ok": ...} / {"error": ...} response, or a pickled predicate
# request) is at most a few MiB even with a generous workload-defined
# state. A compromised child subprocess can write any 4-byte prefix
# (including 0xFFFFFFFF); without a cap, the parent would attempt to
# allocate a 4 GiB buffer for ``stream.read(n)`` before discovering
# the truncation. 64 MiB is large enough for any realistic frame and
# small enough that a malicious frame cannot exhaust the host.
_FRAME_MAX_BODY_BYTES: int = 64 * 1024 * 1024


def _write_frame(stream: Any, data: bytes) -> None:
    stream.write(_FRAME_LEN.pack(len(data)))
    stream.write(data)
    stream.flush()


def _read_frame(stream: Any) -> bytes | None:
    header = stream.read(_FRAME_LEN.size)
    if not header:
        return None
    if len(header) != _FRAME_LEN.size:
        # A child that printed fewer than 4 bytes before exiting (a
        # stray `print()` during import + crash) gives a truncated
        # header. Mirror the body-truncation branch below so the
        # documented `SkillContractExecutorError` boundary is upheld
        # instead of leaking `struct.error`.
        raise SkillContractExecutorError(
            f"truncated frame header from subprocess: "
            f"expected {_FRAME_LEN.size} bytes, got {len(header)}"
        )
    (n,) = _FRAME_LEN.unpack(header)
    if n > _FRAME_MAX_BODY_BYTES:
        # BL-216: a compromised child subprocess can encode any
        # ``n`` up to 2**32-1 in the frame header; without this cap
        # the parent would attempt a ~4 GiB allocation on
        # ``stream.read(n)`` before discovering the truncation,
        # exhausting host memory. Refuse the frame at the documented
        # SkillContractExecutorError boundary; the parent caller
        # already kills the subprocess on error.
        raise SkillContractExecutorError(
            f"oversize frame from subprocess: header claims {n} bytes (cap {_FRAME_MAX_BODY_BYTES})"
        )
    body = stream.read(n)
    if len(body) != n:
        raise SkillContractExecutorError(
            f"truncated frame from subprocess: expected {n} bytes, got {len(body)}"
        )
    return cast(bytes, body)


@dataclass(frozen=True)
class _PredicateProxy:
    """A Predicate that forwards ``__call__`` to a subprocess.

    Carries ``name`` and ``severity`` so the Predicate Protocol is
    satisfied for free; ``__call__`` ships the state over IPC and
    returns the bool / raised exception.
    """

    name: str
    severity: Severity
    _slot: str = field(repr=False)
    _evaluator: _SubprocessEvaluator = field(repr=False)

    def __call__(self, state: Any) -> bool:
        return self._evaluator.evaluate(self._slot, self.name, state)


class _SubprocessEvaluator:
    """Owns the long-lived child process and the IPC lock.

    One evaluator per ``Skill.contract()`` call; the same process
    serves every predicate evaluation. ``close()`` is idempotent and
    safe from a finalizer / atexit / explicit teardown.
    """

    def __init__(
        self,
        contract_path: Path,
        *,
        cpu_seconds: int,
        memory_mb: int,
        open_files: int,
        timeout_seconds: float,
        python_executable: str | None,
    ) -> None:
        self._lock = threading.Lock()
        self._timeout = timeout_seconds
        self._proc: subprocess.Popen[bytes] | None = self._spawn(
            contract_path,
            cpu_seconds=cpu_seconds,
            memory_mb=memory_mb,
            open_files=open_files,
            python_executable=python_executable,
        )
        self._closed = False

    @staticmethod
    def _spawn(
        contract_path: Path,
        *,
        cpu_seconds: int,
        memory_mb: int,
        open_files: int,
        python_executable: str | None,
    ) -> subprocess.Popen[bytes]:
        env: dict[str, str] = {}
        # Do not forward the full parent environment (contains
        # operator secrets such as model/provider credentials). Keep a
        # minimal runtime envelope only.
        for key in (
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TMPDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
        ):
            value = os.environ.get(key)
            if value is not None:
                env[key] = value
        # Hand the child the limits via env; setrlimit happens in the
        # child's preexec stage (POSIX) or via the child's own startup
        # code (the child reads its own env on entry).
        env["AGENTS_SKILL_CPU_SECONDS"] = str(cpu_seconds)
        env["AGENTS_SKILL_MEMORY_MB"] = str(memory_mb)
        env["AGENTS_SKILL_OPEN_FILES"] = str(open_files)
        env["AGENTS_SKILL_CONTRACT_PATH"] = str(contract_path)
        py = python_executable or sys.executable
        return subprocess.Popen(
            [py, "-m", "skills._executor_child"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            close_fds=True,
            start_new_session=True,
        )

    def load_metadata(self) -> dict[str, Any]:
        """Wait for the child's initial frame: contract metadata.

        Returns the parsed JSON: ``{"name", "version", "preconditions"
        [...], "postconditions" [...], "invariants" [...],
        "governance" [...]}``. The lists contain per-predicate
        ``{"name", "severity"}``.
        """
        if (
            self._proc is None or self._proc.stdout is None or self._proc.stdin is None
        ):  # pragma: no cover - defensive: Popen always returns pipes when stdin/stdout=PIPE
            raise SkillContractExecutorError("subprocess not started")
        # The child writes a single frame on startup. Read with a
        # bounded timeout via a poll thread so a hung child does not
        # hang the parent forever.
        frame = self._read_with_timeout(self._proc.stdout)
        if frame is None:
            stderr = b""
            if self._proc.stderr is not None:
                stderr = self._proc.stderr.read() or b""
            raise SkillContractExecutorError(
                f"subprocess produced no output (exit={self._proc.poll()!r}): "
                f"stderr={stderr.decode(errors='replace')!r}"
            )
        # The child only writes JSON via json.dumps, so the decode
        # error branch is defensive; mark no cover on the except.
        try:
            data = json.loads(frame.decode("utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover
            raise SkillContractExecutorError(
                f"subprocess returned non-JSON metadata: {frame!r}"
            ) from exc
        if "error" in data:
            # Child surfaced a structured error during load (no
            # contract.py, no `contract` export, import raised, etc.).
            # Map to SkillManifestError to preserve the L1 contract.
            raise SkillManifestError(str(data.get("path", "")), str(data["error"]))
        return cast(dict[str, Any], data)

    def evaluate(self, slot: str, name: str, state: Any) -> bool:
        if self._closed or self._proc is None:
            raise SkillContractExecutorError("evaluator is closed")
        if (
            self._proc.stdin is None or self._proc.stdout is None
        ):  # pragma: no cover - defensive: Popen always returns pipes when stdin/stdout=PIPE
            raise SkillContractExecutorError("subprocess pipes are closed")
        # If the child has already exited (e.g., RLIMIT_CPU killed it
        # between calls), surface a documented executor error instead
        # of letting `_write_frame` raise BrokenPipeError. ``poll()``
        # returns None while alive.
        exit_code = self._proc.poll()
        if exit_code is not None:
            stderr = b""
            if self._proc.stderr is not None:
                stderr = self._proc.stderr.read() or b""
            raise SkillContractExecutorError(
                f"subprocess already exited (exit={exit_code!r}) before "
                f"predicate {name!r}: stderr={stderr.decode(errors='replace')!r}"
            )
        request = {"op": "evaluate", "slot": slot, "name": name}
        payload = pickle.dumps((request, state))
        with self._lock:
            # The poll() check above is the primary detector for an
            # already-exited child; this branch handles the rare race
            # where the child died between poll() and write. Defensive.
            try:
                _write_frame(self._proc.stdin, payload)
            except (BrokenPipeError, OSError) as exc:  # pragma: no cover
                stderr = b""
                if self._proc.stderr is not None:
                    stderr = self._proc.stderr.read() or b""
                raise SkillContractExecutorError(
                    f"subprocess write failed for predicate {name!r}: {exc!r}; "
                    f"stderr={stderr.decode(errors='replace')!r}"
                ) from exc
            frame = self._read_with_timeout(self._proc.stdout)
        if frame is None:
            stderr = b""
            if self._proc.stderr is not None:
                stderr = self._proc.stderr.read() or b""
            raise SkillContractExecutorError(
                f"subprocess exited mid-evaluation (exit={self._proc.poll()!r}): "
                f"stderr={stderr.decode(errors='replace')!r}"
            )
        # The child only writes JSON via json.dumps; this is defensive.
        try:
            resp = json.loads(frame.decode("utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover
            raise SkillContractExecutorError(
                f"subprocess returned non-JSON response: {frame!r}"
            ) from exc
        if "error" in resp:
            raise SkillContractExecutorError(
                f"predicate {name!r} raised in subprocess: {resp['error']}"
            )
        return bool(resp["ok"])

    def _read_with_timeout(self, stream: Any) -> bytes | None:
        result: list[bytes | BaseException | None] = []

        def _worker() -> None:
            try:
                result.append(_read_frame(stream))
            except BaseException as exc:
                result.append(exc)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=self._timeout)
        if t.is_alive():
            # The subprocess is wedged. Kill it and surface a
            # diagnostic; the timeout is a load-time / per-evaluate
            # cap so a runaway predicate cannot hang the harness.
            self._kill_subprocess()
            raise SkillContractExecutorError(
                f"subprocess timed out after {self._timeout:.2f}s waiting for a frame"
            )
        if not result:
            return None
        item = result[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def _kill_subprocess(self) -> None:
        import contextlib
        import signal

        if self._proc is None:
            return
        pid = self._proc.pid
        if os.name == "posix":
            with contextlib.suppress(Exception):
                os.killpg(pid, signal.SIGKILL)
            return
        with contextlib.suppress(Exception):
            self._proc.kill()

    def close(self) -> None:
        import contextlib

        if self._closed:
            return
        self._closed = True
        if self._proc is not None:
            with self._lock:
                # Close stdin so the child sees EOF and exits cleanly;
                # then wait briefly, kill on timeout.
                if self._proc.stdin is not None:
                    with contextlib.suppress(Exception):
                        self._proc.stdin.close()
                try:
                    self._proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self._kill_subprocess()


@dataclass(frozen=True)
class SubprocessSkillContractExecutor:
    """Load and evaluate the contract in a subprocess with rlimits.

    Crash isolation is real: a contract that ``sys.exit``s, segfaults,
    or recurses past the stack limit raises a
    ``SkillContractExecutorError`` in the parent but does not kill the
    harness. Resource isolation is bounded: ``cpu_seconds`` /
    ``memory_mb`` / ``open_files`` are set via
    ``resource.setrlimit`` in the child (POSIX); on Windows or a
    platform that does not honour the rlimit, the executor still
    delivers crash isolation but the resource cap is best-effort.

    Capability isolation (filesystem, network, syscalls) is NOT
    enforced by this executor: the child inherits the parent's
    namespace. Operators who need real isolation (untrusted bundles
    over the wire) compose this executor with an OS-level mechanism
    (container, seccomp, mount namespace) out-of-band, or supply
    their own ``SkillContractExecutor`` implementation; the Protocol
    is the in-tree extension point.

    ``timeout_seconds`` bounds every IPC round-trip (load + each
    predicate evaluation) so a hung child cannot wedge the harness.
    """

    name: str = "subprocess"
    cpu_seconds: int = 10
    memory_mb: int = 256
    open_files: int = 64
    timeout_seconds: float = 30.0
    python_executable: str | None = None

    def load(self, skill: Skill) -> Contract[Any, Any] | None:
        from harness.contract import Contract, Severity

        if skill.contract_path is None:
            return None
        evaluator = _SubprocessEvaluator(
            skill.contract_path,
            cpu_seconds=self.cpu_seconds,
            memory_mb=self.memory_mb,
            open_files=self.open_files,
            timeout_seconds=self.timeout_seconds,
            python_executable=self.python_executable,
        )
        try:
            meta = evaluator.load_metadata()
        except BaseException:
            evaluator.close()
            raise

        def _proxies(slot: str) -> list[Any]:
            # BL-217: validate every item from the subprocess metadata
            # frame at the parent-child trust boundary. A buggy or
            # malicious contract that constructs a non-conforming
            # `Contract` whose predicate has a name/severity outside
            # the expected shape would, without this check, surface as
            # an unstructured `KeyError` or `ValueError` from the
            # ``Severity(sev_str)`` conversion below. Translate every
            # malformed-item case to the documented
            # `SkillContractExecutorError` so callers see a single
            # exception boundary across in-process and subprocess
            # executors.
            out: list[Any] = []
            for item in meta.get(slot, []):
                if not isinstance(item, dict):
                    raise SkillContractExecutorError(
                        f"malformed metadata item in slot {slot!r}: "
                        f"expected dict, got {type(item).__name__}"
                    )
                try:
                    name = item["name"]
                    sev_str = item["severity"]
                except KeyError as exc:
                    raise SkillContractExecutorError(
                        f"malformed metadata item in slot {slot!r}: missing key {exc!s}"
                    ) from exc
                if not isinstance(name, str) or not isinstance(sev_str, str):
                    raise SkillContractExecutorError(
                        f"malformed metadata item in slot {slot!r}: name/severity must be str "
                        f"(got name={type(name).__name__}, severity={type(sev_str).__name__})"
                    )
                try:
                    sev = Severity(sev_str)
                except ValueError as exc:
                    raise SkillContractExecutorError(
                        f"malformed metadata item in slot {slot!r}: unknown severity {sev_str!r}"
                    ) from exc
                out.append(
                    _PredicateProxy(
                        name=name,
                        severity=sev,
                        _slot=slot,
                        _evaluator=evaluator,
                    )
                )
            return out

        contract = Contract(
            name=str(meta.get("name", skill.name)),
            version=str(meta.get("version", "0.0.0")),
            preconditions=_proxies("preconditions"),
            invariants=_proxies("invariants"),
            postconditions=_proxies("postconditions"),
            governance=_proxies("governance"),
        )
        # Lifecycle: tie the subprocess to the returned Contract via
        # ``weakref.finalize``. The finalizer runs at the first of:
        # (a) the Contract being GC'd (mid-process cleanup), or
        # (b) interpreter exit (the finalizer is auto-registered with
        # atexit by weakref.finalize). Without this hook, a load that
        # leaves the returned Contract unreferenced would orphan the
        # subprocess until process exit; the Copilot review on PR #56
        # raised this as a real leak on repeated `Skill.contract()`
        # calls. The proxy predicates keep ``evaluator`` alive while
        # the Contract is alive (they reference it as a field), so
        # finalize fires only after every reference to the Contract
        # is gone.
        weakref.finalize(contract, evaluator.close)
        return contract
