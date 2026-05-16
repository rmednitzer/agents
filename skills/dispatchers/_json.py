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
    """Every top-level ``[...]`` span, bracket-balanced, string-aware."""
    spans: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "[":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    spans.append(text[i : j + 1])
                    break
            j += 1
        # Continue scanning after this '[' (overlapping starts are fine;
        # the next real array, if any, will be found independently).
        i += 1
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
