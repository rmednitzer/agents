#!/usr/bin/env python3
"""Generate JSON Schema artifacts from the harness Pydantic models.

Purpose:
    Emit ``WorkloadManifest.model_json_schema()`` to
    ``docs/schema/workload-manifest.json`` so editors can offer
    autocomplete and validation against ``manifest.yaml`` (BL-013,
    ADR 0007). The skill manifest schema is emitted alongside it.

Usage:
    python scripts/gen_schema.py            # (re)write the schema files
    python scripts/gen_schema.py --check    # exit 1 if any file is stale

The ``--check`` mode is what CI and the test suite use to guard against
a model change that was not regenerated. Output is deterministic
(sorted keys, two-space indent, trailing newline) so the diff is stable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Make the repo root importable when run as a bare script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills.types import SkillManifest  # noqa: E402
from workloads.manifest import WorkloadManifest  # noqa: E402

_SCHEMA_DIR = _REPO_ROOT / "docs" / "schema"

# model -> output filename. Add new rows as schemas are needed.
_TARGETS: dict[str, Any] = {
    "workload-manifest.json": WorkloadManifest,
    "skill-manifest.json": SkillManifest,
}


def _render(model: Any) -> str:
    """Serialize a model's JSON Schema deterministically."""
    schema = model.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def generate(check: bool) -> int:
    """Write or verify every schema target.

    Returns a process exit code: 0 on success, 1 if ``check`` is set and
    at least one committed file is missing or stale.
    """
    _SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for filename, model in _TARGETS.items():
        path = _SCHEMA_DIR / filename
        rendered = _render(model)
        if check:
            current = path.read_text(encoding="utf-8") if path.is_file() else None
            if current != rendered:
                stale.append(filename)
        else:
            path.write_text(rendered, encoding="utf-8")
            print(f"wrote {path.relative_to(_REPO_ROOT)}")
    if check and stale:
        print(
            "stale schema files (run `make schema`): " + ", ".join(sorted(stale)),
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="gen_schema", description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify committed schemas are current; do not write",
    )
    args = parser.parse_args()
    return generate(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
