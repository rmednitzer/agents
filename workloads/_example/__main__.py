"""Entry point for the _example workload.

Provides a stub Runtime that performs the markdown validation in-process
(no LLM call) and a CLI wrapper. Real workloads use a Runtime adapter
that invokes an agent framework; this bundle demonstrates the bundle
convention without requiring an API key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from harness import run_under_contract
from harness.budgets import BudgetTracker
from harness.guard import ToolGuard
from harness.interruption import ResumableState
from harness.mcp import MCPServerSpec
from harness.runtime import Runtime
from workloads._example.contract import (
    Finding,
    ValidationInput,
    ValidationReport,
    contract,
)

__all__ = ["MarkdownValidatorRuntime", "main"]


def _double_dash_outside_comment(line: str, in_comment: bool) -> tuple[bool, bool]:
    """Position-aware scan for ``--`` outside ``<!-- ... -->`` spans (`BL-211`).

    Returns ``(found, in_comment_after_line)``. Walks the line
    left-to-right, alternating between outside-comment and
    inside-comment regions, and reports True iff an outside region
    contains ``--``. The follow-up state carries the open-comment
    flag across lines so a multi-line ``<!-- ... -->`` block is
    handled correctly.

    Pre-`BL-211` the validator used three per-line ``in <line>``
    checks with a single boolean tracker; on a line like
    ``foo -- bar <!-- baz -->`` the ``<!--`` check fired first and
    set the flag, causing the subsequent outside-comment ``--`` to
    be missed (and symmetrically for a line that closes a comment
    and then carries prose ``--`` after ``-->``).
    """
    pos = 0
    found = False
    n = len(line)
    while pos < n:
        if in_comment:
            end = line.find("-->", pos)
            if end == -1:
                return found, True
            pos = end + 3
            in_comment = False
        else:
            start = line.find("<!--", pos)
            outside_end = start if start != -1 else n
            if "--" in line[pos:outside_end]:
                found = True
            if start == -1:
                return found, False
            pos = start + 4
            in_comment = True
    return found, in_comment


class MarkdownValidatorRuntime:
    """In-process stub Runtime that scans markdown for style violations."""

    name: str = "markdown-validator-stub"

    async def run(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
        resume: ResumableState | None = None,
    ) -> ValidationReport:
        input_data = ValidationInput.model_validate_json(prompt)
        findings: list[Finding] = []

        lines = input_data.content.splitlines()
        in_html_comment = False
        for i, line in enumerate(lines, start=1):
            if "—" in line:
                findings.append(
                    Finding(
                        rule="no-em-dash",
                        line=i,
                        message="line contains em-dash",
                    )
                )
            has_dd, in_html_comment = _double_dash_outside_comment(line, in_html_comment)
            if has_dd:
                findings.append(
                    Finding(
                        rule="no-double-dash",
                        line=i,
                        message="line contains '--' outside HTML comment",
                    )
                )

        stripped = input_data.content.lstrip()
        first_line = stripped.split("\n", 1)[0] if stripped else ""
        if not first_line.startswith("# "):
            findings.append(
                Finding(
                    rule="h1-required",
                    line=1,
                    message="document does not start with an H1 heading",
                )
            )

        return ValidationReport(
            document_name=input_data.document_name,
            passed=len(findings) == 0,
            findings=findings,
        )

    def stream(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        deps: Any | None = None,
        budget: BudgetTracker | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        guard: ToolGuard | None = None,
        resume: ResumableState | None = None,
    ) -> AsyncIterator[Any]:
        raise NotImplementedError("MarkdownValidatorRuntime does not support streaming")


async def main(content: str, document_name: str = "untitled") -> ValidationReport:
    """Run the _example workload against a markdown document.

    Args:
        content: Raw markdown content.
        document_name: Identifier for the document (for the report).

    Returns:
        ValidationReport with passed flag and any findings.
    """
    runtime: Runtime = MarkdownValidatorRuntime()
    result = await run_under_contract(
        runtime=runtime,
        contract=contract,
        input=ValidationInput(content=content, document_name=document_name),
        output_model=ValidationReport,
    )
    assert isinstance(result, ValidationReport)
    return result


def _cli() -> None:
    parser = argparse.ArgumentParser(
        prog="workloads._example",
        description="Validate a markdown document against contract style conventions.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="path to a markdown file. Reads from stdin if omitted.",
    )
    args = parser.parse_args()
    if args.path:
        content = Path(args.path).read_text()
        doc_name = args.path
    else:
        content = sys.stdin.read()
        doc_name = "<stdin>"
    report = asyncio.run(main(content, doc_name))
    print(json.dumps(report.model_dump(), indent=2))


if __name__ == "__main__":
    _cli()
