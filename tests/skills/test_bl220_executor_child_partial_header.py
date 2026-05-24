"""BL-220 (eighth audit, ADR 0018): child-side partial IPC header.

Class extension of BL-216 on the child side. The child's
``skills._executor_child._read_frame`` handled the empty-header case
(parent closed stdin cleanly) and the oversize-header case (BL-216:
defence in depth against a corrupted parent pipe), but a 1-3 byte
partial header (parent crashed mid-write after sending 1, 2, or 3 of
the 4 length-prefix bytes) raised `struct.error` from
`_FRAME_LEN.unpack(header)`. That crashed the child with an unhandled
exception instead of the clean main-loop exit the empty-header path
takes.

The fix mirrors the empty-header check: a header whose length is not
exactly `_FRAME_LEN.size` is treated as EOF, parallel to the parent's
own truncated-header rejection at the documented
`SkillContractExecutorError` boundary in `skills.execution._read_frame`.
"""

from __future__ import annotations

import io

from skills._executor_child import _read_frame


def test_partial_header_one_byte_treated_as_eof() -> None:
    stream = io.BytesIO(b"\x00")
    assert _read_frame(stream) is None


def test_partial_header_two_bytes_treated_as_eof() -> None:
    stream = io.BytesIO(b"\x00\x00")
    assert _read_frame(stream) is None


def test_partial_header_three_bytes_treated_as_eof() -> None:
    stream = io.BytesIO(b"\x00\x00\x00")
    assert _read_frame(stream) is None


def test_complete_empty_input_still_treated_as_eof() -> None:
    # Sanity: the BL-216 empty-header path is unchanged.
    stream = io.BytesIO(b"")
    assert _read_frame(stream) is None


def test_complete_valid_frame_still_decodes() -> None:
    # Sanity: the happy path is unchanged. 4-byte big-endian length 5,
    # then 5 bytes of body.
    stream = io.BytesIO(b"\x00\x00\x00\x05hello")
    assert _read_frame(stream) == b"hello"
