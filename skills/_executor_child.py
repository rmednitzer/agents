"""Subprocess entry point for SubprocessSkillContractExecutor (BL-133).

Reads the contract path and resource limits from the environment,
applies the limits (POSIX), imports the contract module, ships its
metadata to the parent on stdout, then services predicate-evaluation
requests on stdin until EOF.

IPC framing matches ``skills.execution``: a 4-byte big-endian length
prefix followed by the body. Parent->child bodies are pickled tuples
``(request_dict, state)``; child->parent bodies are JSON. The child
never writes pickled data to the parent so a malicious contract
module cannot RCE the harness through this channel.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pickle
import struct
import sys
import traceback
from pathlib import Path
from typing import Any, cast

_FRAME_LEN = struct.Struct(">I")


def _write_frame(stream: Any, data: bytes) -> None:
    stream.write(_FRAME_LEN.pack(len(data)))
    stream.write(data)
    stream.flush()


def _read_frame(stream: Any) -> bytes | None:
    header = stream.read(_FRAME_LEN.size)
    if not header:
        return None
    (n,) = _FRAME_LEN.unpack(header)
    body = stream.read(n)
    if len(body) != n:
        return None
    return cast(bytes, body)


def _apply_rlimits(cpu_seconds: int, memory_mb: int, open_files: int) -> None:
    """Apply CPU / memory / open-files caps (POSIX best-effort).

    Windows has no ``resource`` module; the executor still delivers
    crash isolation there, but the rlimit cap is silently skipped.
    The parent's docstring documents the platform contract.
    """
    import contextlib

    try:
        import resource
    except ImportError:  # pragma: no cover - platform-dependent
        return
    # CPU time: when exceeded, the kernel sends SIGXCPU then SIGKILL.
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    # Address space (process memory). Linux honours RLIMIT_AS; macOS
    # ignores it consistently, hence "best-effort".
    with contextlib.suppress(ValueError, OSError):
        bytes_cap = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (bytes_cap, bytes_cap))
    # Open file descriptors (RLIMIT_NOFILE). We cannot lower below
    # the soft limit on some setups; bound it to the smaller of
    # the requested cap and the current hard limit.
    with contextlib.suppress(ValueError, OSError):
        _cur_soft, cur_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        cap = min(open_files, cur_hard) if cur_hard != resource.RLIM_INFINITY else open_files
        resource.setrlimit(resource.RLIMIT_NOFILE, (cap, cap))


def _load_contract_module(path: Path) -> Any:
    """Import the contract module from ``path``.

    Mirrors ``skills.loader._load_skill_contract`` but emits a
    structured error frame instead of raising a Python exception out
    of the subprocess (which would just exit the child without the
    parent learning what went wrong).
    """
    spec = importlib.util.spec_from_file_location(f"_subprocess_contract_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot create import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _serialise_metadata(contract: Any) -> dict[str, Any]:
    """Reify just the metadata the parent needs to build proxies.

    The parent does not need (or want) the predicates' callable
    objects -- they stay in this process. Each predicate item is
    ``{"name", "severity"}`` so the parent can build a
    ``_PredicateProxy`` of the right shape.
    """

    def _items(preds: Any) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for p in preds or []:
            sev = p.severity
            # ``Severity`` is a StrEnum so str() returns the value.
            out.append({"name": p.name, "severity": str(sev)})
        return out

    return {
        "name": contract.name,
        "version": contract.version,
        "preconditions": _items(contract.preconditions),
        "invariants": _items(contract.invariants),
        "postconditions": _items(contract.postconditions),
        "governance": _items(contract.governance),
    }


def _find_predicate(contract: Any, slot: str, name: str) -> Any:
    preds = getattr(contract, slot)
    for p in preds:
        if p.name == name:
            return p
    raise KeyError(f"no predicate named {name!r} in slot {slot!r}")


def main() -> int:
    cpu = int(os.environ.get("AGENTS_SKILL_CPU_SECONDS", "10"))
    mem = int(os.environ.get("AGENTS_SKILL_MEMORY_MB", "256"))
    files = int(os.environ.get("AGENTS_SKILL_OPEN_FILES", "64"))
    contract_path = os.environ.get("AGENTS_SKILL_CONTRACT_PATH")

    if not contract_path:
        _write_frame(
            sys.stdout.buffer,
            json.dumps({"error": "AGENTS_SKILL_CONTRACT_PATH not set"}).encode(),
        )
        return 1

    _apply_rlimits(cpu, mem, files)

    path = Path(contract_path)
    try:
        module = _load_contract_module(path)
    except Exception as exc:
        _write_frame(
            sys.stdout.buffer,
            json.dumps({"error": f"import failed: {exc!r}", "path": str(path)}).encode(),
        )
        return 1

    contract = getattr(module, "contract", None)
    if contract is None:
        _write_frame(
            sys.stdout.buffer,
            json.dumps(
                {
                    "error": "contract.py does not export 'contract'",
                    "path": str(path),
                }
            ).encode(),
        )
        return 1
    # Parity with the in-process loader (`skills.loader.
    # _load_skill_contract`): a `contract` export that is not a
    # `harness.contract.Contract` instance is a mis-shaped manifest,
    # not a runtime error. Reject it here so the subprocess executor
    # has the same documented `SkillManifestError` contract as the
    # in-process default (Copilot review on PR #56). The harness
    # package is in the same venv as the parent, so this import
    # always succeeds when the parent's import succeeded.
    from harness.contract import Contract

    if not isinstance(contract, Contract):
        _write_frame(
            sys.stdout.buffer,
            json.dumps(
                {
                    "error": (
                        f"'contract' is not a Contract (got "
                        f"{type(contract).__name__})"
                    ),
                    "path": str(path),
                }
            ).encode(),
        )
        return 1

    # Ship the metadata frame and enter the evaluation loop.
    try:
        meta = _serialise_metadata(contract)
    except Exception as exc:
        _write_frame(
            sys.stdout.buffer,
            json.dumps(
                {
                    "error": f"contract metadata not serialisable: {exc!r}",
                    "path": str(path),
                }
            ).encode(),
        )
        return 1
    _write_frame(sys.stdout.buffer, json.dumps(meta).encode())

    while True:
        frame = _read_frame(sys.stdin.buffer)
        if frame is None:
            return 0  # EOF: parent closed stdin.
        try:
            request, state = pickle.loads(frame)
        except Exception as exc:
            _write_frame(
                sys.stdout.buffer,
                json.dumps({"error": f"unpickle failed: {exc!r}"}).encode(),
            )
            continue
        op = request.get("op")
        if op != "evaluate":
            _write_frame(
                sys.stdout.buffer,
                json.dumps({"error": f"unknown op: {op!r}"}).encode(),
            )
            continue
        slot = request.get("slot", "")
        name = request.get("name", "")
        try:
            pred = _find_predicate(contract, slot, name)
            result = bool(pred(state))
            _write_frame(sys.stdout.buffer, json.dumps({"ok": result}).encode())
        except Exception as exc:
            _write_frame(
                sys.stdout.buffer,
                json.dumps(
                    {
                        "error": (
                            f"{type(exc).__name__}: {exc}\n"
                            + "".join(traceback.format_exception(exc))
                        )
                    }
                ).encode(),
            )


if __name__ == "__main__":
    raise SystemExit(main())
