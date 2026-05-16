"""Tests for the agents CLI (BL-020, BL-021, BL-022)."""

from __future__ import annotations

import json

import pytest

from agents.cli import build_parser, main


def test_parser_requires_a_command() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_bl020_workloads_list_includes_example(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["workloads", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "_example" in out
    assert "0.1.0" in out


def test_bl022_skills_list_grouped_by_lane(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["skills", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[documentation]" in out
    assert "example" in out


def test_bl021_run_example_passes(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["run", "_example", "# Title\n\nclean body\n"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["workload"] == "_example"
    assert payload["result"]["passed"] is True
    assert payload["dispatch"] is None  # _example declares no dispatcher


def test_bl021_run_example_reports_findings(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["run", "_example", "no heading at all"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["result"]["passed"] is False
    rules = {f["rule"] for f in payload["result"]["findings"]}
    assert "h1-required" in rules


def test_bl021_run_unknown_workload_errors(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["run", "_nope_xyz", "q"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err
