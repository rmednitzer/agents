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
    """Every bracket-balanced ``[...]`` span, by opening-bracket order.

    Single linear pass with an explicit stack of open-``[`` indices:
    each character is visited once, so adversarial output (e.g. a
    megabyte of ``[``) cannot make extraction quadratic, while an
    unmatched ``[`` or ``]`` in prose no longer suppresses a later valid
    array: the stack still matches the real array's own brackets, and a
    stray ``]`` outside any span is ignored, not underflowed. Spans are
    returned ordered by their opening bracket, matching the pre-rewrite
    per-candidate scan, so ``first_json_array`` still prefers the
    earliest/outermost array. String state is tracked only inside a
    span (stack non-empty), so an unmatched ``"`` in leading prose
    cannot desync parsing. A rarer compound fault (an unmatched ``[``
    *and* an unmatched ``"`` both in prose before the array) degrades to
    the documented None / parse-error fallback rather than reintroducing
    the O(n^2) per-``[`` restart.
    """
    spans: list[tuple[int, str]] = []
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
            spans.append((opened, text[opened : idx + 1]))
            if not stack:
                in_str = False
                esc = False
    spans.sort(key=lambda s: s[0])
    return [span for _, span in spans]


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
