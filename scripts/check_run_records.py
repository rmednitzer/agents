#!/usr/bin/env python3
"""Offline gate over a corpus of persisted RunRecords (BL-185, ADR 0012).

Re-validates a directory of ``*.run.json`` provenance records the way
``sentinel``'s ``validate_artifacts.py`` + ``check_provenance.py``
re-validate its pulse corpus, with the two deliberate divergences this
repo adopted instead of copying:

- The contract digest is the one the enforcement loop stamped in-process
  at run time. This gate compares it against an optional registry of
  ``"name@version" -> expected sha256`` digests (produced by
  ``contract_digest`` over the contracts that *should* have run). There
  is no git round-trip; nothing can be re-stamped after the fact.
- Every finding is a hard error. There is no warn-and-pass tier (the
  defect this gate exists not to reproduce: ``sentinel``'s consistency
  CI routes overlap/coverage findings to warnings and still exits 0,
  so its "blocking" claim is not actually enforced).

Checks per record: parseable JSON; ``schema_version`` supported;
validates against the ``RunRecord`` model (shape + enum + bounds);
``run_id`` non-empty; ``completed_at`` not before ``started_at``
(the run-identity monotonicity invariant); and, when a registry is
given, ``contract_digest`` matches the expected digest for
``contract_name@contract_version`` (a record citing an unknown
contract is itself an error, never a silent pass).

Usage:
    python scripts/check_run_records.py <dir> [--registry registry.json]

Exit codes:
    0  every record sound (or the directory has no records yet)
    1  one or more hard violations
    2  bad invocation (missing directory)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_DIGEST_RE = re.compile(r"\A[0-9a-f]{64}\Z")

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pydantic import ValidationError  # noqa: E402

from harness.provenance import (  # noqa: E402
    SUPPORTED_RUN_RECORD_SCHEMA_VERSIONS,
    RunRecord,
    record_invariant_violations,
)


def _check_record(path: Path, registry: dict[str, str] | None) -> list[str]:
    """Return the list of hard violations for one record file."""
    errors: list[str] = []
    # ValueError covers JSONDecodeError and UnicodeDecodeError (a bad
    # byte sequence in a persisted artifact must be a per-file
    # violation, not a process-level traceback).
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{path}: unreadable / invalid JSON: {exc}"]

    if not isinstance(raw, dict):
        return [f"{path}: top-level JSON is {type(raw).__name__}, expected a RunRecord object"]

    # Validate the model first, then check the (post-default) version.
    # Doing it in this order means a record that omits schema_version
    # is the current version for both the model and this gate (they
    # agree), and a non-scalar schema_version fails model validation
    # rather than raising on the set-membership test.
    try:
        record = RunRecord.model_validate(raw)
    except ValidationError as exc:
        return [f"{path}: does not validate against RunRecord: {exc}"]

    if record.schema_version not in SUPPORTED_RUN_RECORD_SCHEMA_VERSIONS:
        return [
            f"{path}: schema_version {record.schema_version!r} not in supported set "
            f"{sorted(SUPPORTED_RUN_RECORD_SCHEMA_VERSIONS)}"
        ]

    # Contract-independent invariants (run id, UTC timestamps,
    # monotonic window) come from the shared helper so this gate and
    # the in-process verify_run_record never diverge.
    errors.extend(f"{path}: {m}" for m in record_invariant_violations(record))

    if registry is not None:
        key = f"{record.contract_name}@{record.contract_version}"
        expected = registry.get(key)
        if expected is None:
            errors.append(
                f"{path}: record cites contract {key!r} which is not in the "
                f"registry (cannot attest provenance)"
            )
        elif expected != record.contract_digest:
            errors.append(
                f"{path}: contract_digest {record.contract_digest} does not "
                f"match the registry digest {expected} for {key!r}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(prog="check_run_records", description=__doc__)
    parser.add_argument("directory", help="directory scanned recursively for *.run.json")
    parser.add_argument(
        "--registry",
        help="JSON file mapping 'name@version' -> expected sha256 contract digest",
    )
    args = parser.parse_args()

    root = Path(args.directory)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    registry: dict[str, str] | None = None
    if args.registry:
        try:
            registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
        # ValueError covers JSONDecodeError and UnicodeDecodeError, so a
        # corrupt registry is the documented invocation failure (exit 2),
        # not a traceback (parity with the per-record read path).
        except (OSError, ValueError) as exc:
            print(f"error: cannot read registry {args.registry}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(registry, dict):
            print(
                f"error: registry {args.registry} is not a JSON object "
                f"(expected 'name@version' -> digest mapping)",
                file=sys.stderr,
            )
            return 2
        # Validate every value is a canonical lowercase 64-hex digest,
        # mirroring the RunRecord model's own ``contract_digest``
        # strictness. Without this a non-canonical registry (uppercase
        # hex, a JSON number, an explicit null) never equals a
        # model-normalised digest, so the gate is silently
        # unsatisfiable and reports a nonsensical "digest 123" message
        # instead of naming the malformed registry entry (a
        # config-trust-boundary failure, exit 2).
        bad = [k for k, v in registry.items() if not (isinstance(v, str) and _DIGEST_RE.match(v))]
        if bad:
            print(
                f"error: registry {args.registry} has non-canonical digest "
                f"value(s) for key(s) {sorted(bad)!r} (expected lowercase "
                f"64-hex sha256)",
                file=sys.stderr,
            )
            return 2

    files = sorted(root.rglob("*.run.json"))
    if not files:
        print(f"no *.run.json records under {root}, nothing to check")
        return 0

    errors: list[str] = []
    for path in files:
        errors.extend(_check_record(path, registry))

    if errors:
        print(f"FAIL: {len(errors)} violation(s) across {len(files)} record(s)")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"PASS: {len(files)} run record(s) sound")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
