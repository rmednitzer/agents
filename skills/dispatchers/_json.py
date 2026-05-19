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

# The DoS bound is on parse *work*, not candidate *count*. A single
# oversized span (a deeply nested blob, the O(n^2) trap) is skipped by
# an O(1) length check without ever being sliced or parsed; the total
# bytes actually handed to json.loads is capped. Bounding the count
# instead would wrongly reject a valid array that legitimately appears
# after many small leading bracket fragments (e.g. repeated "[note]"
# preamble) while still being cheap to scan. A real top-level
# dispatch array is a few KB; these bounds are far above that and only
# bite adversarial input.
_MAX_CANDIDATE_BYTES = 64 * 1024
_MAX_TOTAL_PARSE_BYTES = 1024 * 1024

# The parse-work bounds above cap bytes handed to ``json.loads``, but
# ``_balanced_spans`` materialises one ``(open, end)`` int-pair per
# closing bracket *before* ``first_json_array`` runs, so a body that is
# mostly brackets (``"[]" * n``) costs O(n) span tuples (~120 B each, a
# ~30x amplification over the source text) regardless of the parse
# budget: a memory-amplification axis the count-vs-work reasoning above
# does not cover. This hard ceiling bounds that list. A real dispatch
# array has a handful of brackets; exceeding this is adversarial and
# correctly degrades to the malformed-input / DispatchError contract
# (the same posture as the oversized-span and RecursionError paths).
_MAX_SPANS = 65536


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
    only spans within ``_MAX_CANDIDATE_BYTES`` / a cumulative
    ``_MAX_TOTAL_PARSE_BYTES`` budget, lazily. The recorded list itself
    is capped at ``_MAX_SPANS`` (a memory ceiling for a bracket-heavy
    body; see the constant's note).

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
            if len(spans) < _MAX_SPANS:
                spans.append((opened, idx + 1))
            if not stack:
                in_str = False
                esc = False
    return spans


def first_json_array(text: str) -> str | None:
    """Return the first balanced JSON array substring, or None.

    Spans are tried in opening-bracket order, so the earliest/outermost
    array is preferred (a real top-level array is always first). The
    first span that ``json.loads`` parses to a ``list`` is returned. A
    span larger than ``_MAX_CANDIDATE_BYTES`` is skipped by an O(1)
    length check (the deeply-nested O(n^2) trap, never sliced/parsed),
    and the total bytes parsed is capped at ``_MAX_TOTAL_PARSE_BYTES``;
    every small span is still tried, so a valid array that legitimately
    follows many small leading bracket fragments is found. If none
    parse, the earliest balanced span is returned so the caller's own
    ``json.loads`` raises its existing, contextual error. A
    pathologically deep span that makes the stdlib decoder exceed the
    recursion limit is treated like a parse error (malformed input, not
    a framework fault), so ``dispatch`` still degrades to its
    DispatchError contract rather than letting ``RecursionError``
    escape.
    """
    spans = _balanced_spans(text)
    if not spans:
        return None
    # Opening-bracket order. The sort is O(n log n) over index-pair
    # tuples on bounded model/tool output (microseconds), not the DoS
    # vector: the original O(n^2) was per-candidate byte
    # materialisation/parse of nested giants, now bounded by the O(1)
    # size skip and the cumulative parse budget below.
    spans.sort()
    budget = _MAX_TOTAL_PARSE_BYTES
    for opened, end in spans:
        size = end - opened
        if size > _MAX_CANDIDATE_BYTES or budget < size:
            # Skip (do not abort): spans are in opening order, not size
            # order, so a later smaller span can still fit the remaining
            # budget and hold the valid array. Both checks are O(1).
            continue
        budget -= size
        candidate = text[opened:end]
        try:
            if isinstance(json.loads(candidate), list):
                return candidate
        except (json.JSONDecodeError, RecursionError):
            continue
    return text[spans[0][0] : spans[0][1]]
