"""Tests for the generated JSON Schema artifacts (BL-013)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from workloads.manifest import WorkloadManifest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCHEMA = _REPO_ROOT / "docs" / "schema" / "workload-manifest.json"


def test_workload_manifest_schema_committed() -> None:
    assert _SCHEMA.is_file(), "run `make schema`"


def test_workload_manifest_schema_matches_model() -> None:
    """The committed schema must equal the model's current JSON Schema."""
    committed = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    assert committed == WorkloadManifest.model_json_schema()


def test_schema_describes_required_fields() -> None:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    assert set(schema["required"]) >= {"name", "version", "description", "runtime"}


def test_gen_schema_check_passes() -> None:
    """`gen_schema.py --check` exits 0 when the committed files are current."""
    proc = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "gen_schema.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
