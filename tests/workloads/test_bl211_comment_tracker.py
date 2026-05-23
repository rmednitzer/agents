"""Sixth-audit deferred item: regression test for `BL-211`
(ADR 0015 deferred close).

`BL-211` (MarkdownValidatorRuntime per-line comment tracker):
the validator used a per-line boolean flag plus three position-blind
`in <line>` checks; on a line that opened or closed an HTML comment
AND carried prose ``--`` on the same line, the flag was set / cleared
across the whole line, causing prose ``--`` outside the comment region
to be missed (or, symmetrically, falsely suppressed). The fix walks
each line position-aware (`_double_dash_outside_comment`).
"""

from __future__ import annotations

import pytest

from workloads._example.__main__ import _double_dash_outside_comment, main

# --- Unit tests on the helper ----------------------------------------


def test_helper_dash_then_comment_open_on_one_line_is_flagged() -> None:
    """``foo -- bar <!-- baz -->`` carries a prose ``--`` BEFORE the
    comment open; the helper detects it. Pre-`BL-211` the per-line
    boolean was set by `<!--` first and suppressed the prose ``--``
    on the same line."""
    found, in_comment = _double_dash_outside_comment("foo -- bar <!-- baz -->", False)
    assert found is True
    assert in_comment is False


def test_helper_comment_close_then_dash_on_one_line_is_flagged() -> None:
    """A line that closes a multi-line comment and then carries
    prose ``--`` after ``-->`` flags the prose. Pre-`BL-211` the
    per-line boolean was cleared only AFTER the dash check ran,
    suppressing the prose."""
    found, in_comment = _double_dash_outside_comment("comment ends --> bar -- baz", True)
    assert found is True
    assert in_comment is False


def test_helper_dash_only_inside_comment_is_not_flagged() -> None:
    """``<!-- a -- harmless comment -->`` keeps the ``--`` strictly
    inside; the helper does not flag (regression of the existing
    `test_double_dash_in_html_comment_ignored` behaviour)."""
    found, in_comment = _double_dash_outside_comment("<!-- a -- harmless comment -->", False)
    assert found is False
    assert in_comment is False


def test_helper_multi_line_comment_state_carries_across_lines() -> None:
    """An ``<!--`` opens, the next line carries it; the third closes.
    Prose ``--`` only on lines 1 and 4 (outside) is flagged."""
    found1, c1 = _double_dash_outside_comment("foo <!-- start", False)
    assert found1 is False
    assert c1 is True
    found2, c2 = _double_dash_outside_comment("comment text", c1)
    assert found2 is False
    assert c2 is True
    found3, c3 = _double_dash_outside_comment("end --> bar", c2)
    assert found3 is False
    assert c3 is False
    found4, c4 = _double_dash_outside_comment("plain -- dash", c3)
    assert found4 is True
    assert c4 is False


def test_helper_empty_line_state_preserved() -> None:
    """An empty line preserves the in-comment state without
    flagging."""
    found, in_comment = _double_dash_outside_comment("", True)
    assert found is False
    assert in_comment is True
    found, in_comment = _double_dash_outside_comment("", False)
    assert found is False
    assert in_comment is False


def test_helper_dash_inside_then_outside_on_same_line() -> None:
    """``<!-- foo --> bar --`` has ``--`` inside (between ``<!--``
    and ``-->``) and outside (after ``-->``). The outside one is
    flagged."""
    found, in_comment = _double_dash_outside_comment("<!-- foo --> bar --", False)
    assert found is True
    assert in_comment is False


# --- End-to-end through the workload runtime --------------------------


@pytest.mark.asyncio
async def test_workload_flags_prose_dash_sharing_line_with_comment_open() -> None:
    """Pre-`BL-211` this content was scored passing (the per-line
    flag suppressed the prose ``--``); now the rule fires."""
    content = "# Doc\n\nfoo -- bar <!-- baz -->\n"
    report = await main(content)
    rules = {f.rule for f in report.findings}
    assert "no-double-dash" in rules


@pytest.mark.asyncio
async def test_workload_flags_prose_dash_sharing_line_with_comment_close() -> None:
    content = "# Doc\n\n<!-- start of comment\ncomment continues --> rest -- here\n"
    report = await main(content)
    rules = {f.rule for f in report.findings}
    assert "no-double-dash" in rules


@pytest.mark.asyncio
async def test_workload_still_ignores_dash_strictly_inside_comment() -> None:
    """The existing `test_double_dash_in_html_comment_ignored`
    behaviour is preserved: prose ``--`` strictly inside the comment
    is still not flagged."""
    content = "# Doc\n\n<!-- a -- harmless comment -->\n\nRegular text.\n"
    report = await main(content)
    rules = {f.rule for f in report.findings}
    assert "no-double-dash" not in rules
