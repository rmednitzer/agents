"""Robust JSON-array extraction shared by the LLM-backed dispatchers.

A greedy ``\\[.*\\]`` regex spans the first ``[`` to the *last* ``]``,
so any extra bracketed text in model output (prose, a second example,
a stray ``]``) makes ``json.loads`` fail even when a valid array is
present. ``first_json_array`` instead scans for a *balanced* top-level
array, ignoring brackets inside JSON string literals, and returns the
first such span that parses as a list (falling back to the first
balanced span so the caller still emits its own parse error).
"""

from __future__ import annotations

import json

__all__ = ["first_json_array"]


def _balanced_spans(text: str) -> list[str]:
    """Every top-level ``[...]`` span, bracket-balanced, string-aware.

    Single left-to-right pass: each character is visited once, so the
    cost is linear in ``len(text)``. The earlier nested-restart scan was
    quadratic, so adversarial model output (e.g. a megabyte of ``[``)
    could hang the dispatcher; this cannot. Only depth-zero arrays are
    returned, matching "top-level array" in this module's contract; a
    stray unbalanced ``]`` at depth 0 is ignored rather than underflowing.
    """
    spans: list[str] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for idx, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0:
                spans.append(text[start : idx + 1])
    return spans


def first_json_array(text: str) -> str | None:
    """Return the first balanced JSON array substring, or None.

    Prefers the first span that ``json.loads`` parses to a ``list``; if
    none parse, returns the first balanced span so the caller's own
    ``json.loads`` raises its existing, contextual error.
    """
    spans = _balanced_spans(text)
    if not spans:
        return None
    for span in spans:
        try:
            if isinstance(json.loads(span), list):
                return span
        except json.JSONDecodeError:
            continue
    return spans[0]
