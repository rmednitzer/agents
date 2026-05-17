"""Tests for the shared balanced JSON-array extractor (Codex P2)."""

from __future__ import annotations

import json

from skills.dispatchers._json import first_json_array


def test_none_when_no_array() -> None:
    assert first_json_array("just prose, no array here") is None
    assert first_json_array("") is None


def test_plain_array() -> None:
    assert json.loads(first_json_array('[{"a": 1}]') or "") == [{"a": 1}]


def test_ignores_leading_and_trailing_noise() -> None:
    text = 'Here is the result:\n[{"skill_name": "x"}]\nHope that helps! [end]'
    assert json.loads(first_json_array(text) or "") == [{"skill_name": "x"}]


def test_handles_nested_arrays_of_objects() -> None:
    text = 'noise [ {"k": [1, 2, [3]]}, {"m": []} ] more ] noise'
    assert json.loads(first_json_array(text) or "") == [{"k": [1, 2, [3]]}, {"m": []}]


def test_brackets_inside_strings_do_not_break_balance() -> None:
    text = '[{"rationale": "use ] and [ in text", "n": 1}]'
    parsed = json.loads(first_json_array(text) or "")
    assert parsed[0]["rationale"] == "use ] and [ in text"


def test_prefers_first_parseable_list_over_earlier_non_list() -> None:
    # A bracketed token that is not valid JSON, then the real array.
    text = 'see [step 1] then [{"skill_name": "y", "confidence": 0.5}]'
    assert json.loads(first_json_array(text) or "") == [{"skill_name": "y", "confidence": 0.5}]


def test_greedy_regex_failure_case_now_parses() -> None:
    # The old r"\[.*\]" spanned first '[' to last ']' -> json.loads failed.
    text = '[{"skill_name": "a"}] and then a trailing [note]'
    span = first_json_array(text)
    assert span is not None
    assert json.loads(span) == [{"skill_name": "a"}]


def test_adversarial_unbalanced_input_is_linear_not_quadratic() -> None:
    # 500k '[' is the decompression-bomb analogue for the extractor.
    # The old nested-restart scan was O(n^2): at this size quadratic is
    # ~10^11 char-ops (minutes), the single linear pass is ~ms. A 10s
    # bound has a huge margin over linear yet is unreachable for
    # quadratic, so it proves the complexity class without being
    # wall-clock-flaky on a slow or contended CI runner.
    # (regression: skills audit S2)
    import time

    payload = "[" * 500_000
    start = time.monotonic()
    assert first_json_array(payload) is None
    assert time.monotonic() - start < 10.0


def test_unbalanced_quote_in_prose_does_not_hide_later_array() -> None:
    # An unmatched '"' in leading prose must not desync quote state and
    # swallow a later valid array: quote state is only tracked inside a
    # span (stack non-empty), matching the pre-rewrite per-candidate scan.
    # (regression: Codex review on PR #25)
    text = 'I suggest "the deployer [{"skill_name": "deployer", "confidence": 0.9}]'
    assert json.loads(first_json_array(text) or "") == [
        {"skill_name": "deployer", "confidence": 0.9}
    ]


def test_unmatched_open_bracket_in_prose_does_not_hide_later_array() -> None:
    # An unmatched '[' in prose must not keep depth above 0 forever and
    # suppress a later valid array; the stack still matches the real
    # array's own brackets and recovers it.
    # (regression: Codex review on PR #25, second finding)
    text = 'note [unfinished prose, then the answer [{"skill_name": "x"}]'
    assert json.loads(first_json_array(text) or "") == [{"skill_name": "x"}]


def test_stray_closing_bracket_does_not_underflow() -> None:
    # Leading unbalanced ']' at depth 0 must be ignored, not drive depth
    # negative and corrupt a later valid array.
    text = ']]] then [{"skill_name": "z"}]'
    assert json.loads(first_json_array(text) or "") == [{"skill_name": "z"}]
