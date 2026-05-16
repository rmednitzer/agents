"""End-to-end tests for the _example workload."""

from __future__ import annotations

import io
import json
import runpy
import sys
from pathlib import Path

import pytest

from harness.errors import PreconditionViolation
from workloads._example import __main__ as example_main
from workloads._example.__main__ import MarkdownValidatorRuntime, main
from workloads._example.contract import (
    ValidationInput,
    ValidationReport,
    contract,
)


@pytest.mark.asyncio
async def test_clean_document_passes() -> None:
    content = "# Clean Document\n\nSome ordinary prose."
    report = await main(content, "clean.md")
    assert isinstance(report, ValidationReport)
    assert report.passed is True
    assert report.findings == []
    assert report.document_name == "clean.md"


@pytest.mark.asyncio
async def test_em_dash_detected() -> None:
    content = "# Doc\n\nThis line has an em-dash — like that."
    report = await main(content)
    assert report.passed is False
    rules = {f.rule for f in report.findings}
    assert "no-em-dash" in rules


@pytest.mark.asyncio
async def test_double_dash_detected() -> None:
    content = "# Doc\n\nThis line has a double-dash -- like that."
    report = await main(content)
    assert report.passed is False
    rules = {f.rule for f in report.findings}
    assert "no-double-dash" in rules


@pytest.mark.asyncio
async def test_h1_required() -> None:
    content = "Plain text without an H1."
    report = await main(content)
    assert report.passed is False
    rules = {f.rule for f in report.findings}
    assert "h1-required" in rules


@pytest.mark.asyncio
async def test_double_dash_in_html_comment_ignored() -> None:
    content = "# Doc\n\n<!-- a -- harmless comment -->\n\nRegular text."
    report = await main(content)
    rules = {f.rule for f in report.findings}
    assert "no-double-dash" not in rules


@pytest.mark.asyncio
async def test_empty_document_violates_precondition() -> None:
    with pytest.raises(PreconditionViolation):
        await main("", "empty.md")


@pytest.mark.asyncio
async def test_whitespace_only_document_violates_precondition() -> None:
    with pytest.raises(PreconditionViolation):
        await main("   \n\n\t\n", "ws.md")


@pytest.mark.asyncio
async def test_multiple_findings_all_collected() -> None:
    content = "No H1 here.\n\nAnd an em-dash —.\nAnd a double-dash --.\n"
    report = await main(content)
    rules = {f.rule for f in report.findings}
    assert "h1-required" in rules
    assert "no-em-dash" in rules
    assert "no-double-dash" in rules
    assert report.passed is False


@pytest.mark.asyncio
async def test_findings_carry_line_numbers() -> None:
    content = "# Doc\n\nSome text.\nEm-dash here —.\n"
    report = await main(content)
    for f in report.findings:
        if f.rule == "no-em-dash":
            assert f.line == 4


def test_contract_exports_expected_predicates() -> None:
    assert contract.name == "_example"
    pre_names = {p.name for p in contract.preconditions}
    post_names = {p.name for p in contract.postconditions}
    assert "content_non_empty" in pre_names
    assert "passed_consistent_with_findings" in post_names


@pytest.mark.asyncio
async def test_runtime_satisfies_protocol() -> None:
    from harness.runtime import Runtime

    rt = MarkdownValidatorRuntime()
    assert isinstance(rt, Runtime)


@pytest.mark.asyncio
async def test_runtime_returns_report_directly() -> None:
    rt = MarkdownValidatorRuntime()
    input_data = ValidationInput(content="# Hi\nClean.", document_name="d")
    result = await rt.run(prompt=input_data.model_dump_json())
    assert isinstance(result, ValidationReport)
    assert result.passed is True


@pytest.mark.asyncio
async def test_stream_not_supported() -> None:
    rt = MarkdownValidatorRuntime()
    with pytest.raises(NotImplementedError, match="does not support streaming"):
        _ = rt.stream(prompt="{}")


def test_cli_reads_file_when_path_provided(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "doc.md"
    path.write_text("# Title\n\nBody.", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["workloads._example", str(path)])
    example_main._cli()
    payload = json.loads(capsys.readouterr().out)
    assert payload["document_name"] == str(path)
    assert payload["passed"] is True


def test_cli_reads_stdin_when_path_omitted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["workloads._example"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("# Title\n\nBody."))
    example_main._cli()
    payload = json.loads(capsys.readouterr().out)
    assert payload["document_name"] == "<stdin>"
    assert payload["passed"] is True


def test_module_entrypoint_runs_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["workloads._example"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("# Title\n\nBody."))
    runpy.run_module("workloads._example.__main__", run_name="__main__")
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
