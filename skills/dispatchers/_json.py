"""Robust JSON-array extraction shared by the LLM-backed dispatchers.

A greedy ``\\[.*\\]`` regex spans the first ``[`` to the *last* ``]``,
so any extra bracketed text in model output (prose, a second example,
a stray ``]``) makes ``json.loads`` fail even when a valid array is
present. ``first_json_array`` instead scans for a *balanced* array,
ignoring brackets inside JSON string literals, and returns the first
such span (by opening position) that parses as a list, falling back to
the earliest balanced span so the caller still emits its own parse
error.
"""

from __future__ import annotations

import json

__all__ = ["first_json_array"]

# Upper bound on how many balanced spans first_json_array will parse.
# A legitimate top-level array always has the smallest opening index so
# it is candidate #1; this cap only bounds work on adversarial input
# (a deeply nested or stray-bracket-heavy blob from an untrusted model
# or MCP tool). Past the cap, extraction degrades to the documented
# parse-error fallback rather than doing unbounded work.
_MAX_CANDIDATES = 64


def _balanced_spans(text: str) -> list[tuple[int, int]]:
    """Balanced ``[...]`` spans as ``(open_idx, end_idx_exclusive)`` pairs.

    Single linear pass with a stack of open-``[`` indices: each
    character is visited once and only an O(1) index pair is recorded
    per closing bracket, so adversarial output cannot make extraction
    super-linear. Recording the matched substring eagerly on every
    nested ``]`` (the prior code) re-materialised O(depth) growing
    slices, so a nested ``[[[...]]]`` model blob was O(n^2) in time and
    memory (a decompression-bomb analogue on untrusted dispatcher
    input); index pairs are O(1) each, and ``first_json_array`` slices
    at most ``_MAX_CANDIDATES`` of them lazily.

    The stack (not a flat depth counter) is what lets an array nested
    inside an *unmatched* prose ``[`` still be recovered: the inner
    array's own brackets still balance on the stack and produce a pair
    even though the outer ``[`` never closes. A stray ``]`` at depth 0
    is ignored (not underflowed), and string state is tracked only
    inside a span so an unmatched ``"`` in leading prose cannot desync
    parsing.
    """
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    in_str = False
    esc = False
    for idx, ch in enumerate(text):
        if not stack:
            # Outside any array: only an opening bracket matters. Quote
            # state is deliberately not tracked at depth 0 so an
            # unbalanced '"' in prose cannot desync a later array.
            if ch == "[":
                stack.append(idx)
            continue
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
            stack.append(idx)
        elif ch == "]":
            opened = stack.pop()
            spans.append((opened, idx + 1))
            if not stack:
                in_str = False
                esc = False
    return spans


def first_json_array(text: str) -> str | None:
    """Return the first balanced JSON array substring, or None.

    Spans are tried in opening-bracket order, so the earliest/outermost
    array is preferred (a real top-level array is always first). The
    first span that ``json.loads`` parses to a ``list`` is returned; if
    none of the first ``_MAX_CANDIDATES`` parse, the earliest balanced
    span is returned so the caller's own ``json.loads`` raises its
    existing, contextual error. A pathologically deep span that makes
    the stdlib decoder exceed the recursion limit is treated like a
    parse error (it is malformed input, not a framework fault), so
    ``dispatch`` still degrades to its DispatchError contract rather
    than letting ``RecursionError`` escape.
    """
    spans = _balanced_spans(text)
    if not spans:
        return None
    spans.sort()  # by opening index, then end: earliest/outermost first
    for opened, end in spans[:_MAX_CANDIDATES]:
        candidate = text[opened:end]
        try:
            if isinstance(json.loads(candidate), list):
                return candidate
        except (json.JSONDecodeError, RecursionError):
            continue
    return text[spans[0][0] : spans[0][1]]
