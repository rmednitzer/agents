"""BL-216 (seventh audit, ADR 0017): subprocess IPC frame length bound.

The 4-byte big-endian length prefix on a parent-child IPC frame can
encode up to ~4 GiB. Without an upper bound, a compromised child
subprocess that writes a header claiming ``2**32-1`` body bytes would
drive the parent into a ~4 GiB ``stream.read(n)`` allocation before
discovering the truncation, exhausting host memory.

The fix caps frames at 64 MiB on both sides:
- Parent (``skills.execution._read_frame``) raises
  ``SkillContractExecutorError`` so the subprocess is killed at the
  documented exception boundary.
- Child (``skills._executor_child._read_frame``) treats an oversize
  header as EOF so the main loop exits cleanly (defence in depth on
  the trusted side).
"""

from __future__ import annotations

import io
import struct

import pytest

from skills._executor_child import _read_frame as child_read_frame
from skills.execution import SkillContractExecutorError
from skills.execution import _read_frame as parent_read_frame

_FRAME_LEN = struct.Struct(">I")


def _header(n: int) -> bytes:
    return _FRAME_LEN.pack(n)


def test_parent_rejects_oversize_header() -> None:
    stream = io.BytesIO(_header(2**31))  # 2 GiB, well above the 64 MiB cap
    with pytest.raises(SkillContractExecutorError, match="oversize frame"):
        parent_read_frame(stream)


def test_parent_rejects_max_uint32_header() -> None:
    # The pathological case the cap exists to catch.
    stream = io.BytesIO(_header(2**32 - 1))
    with pytest.raises(SkillContractExecutorError, match="oversize frame"):
        parent_read_frame(stream)


def test_parent_accepts_legitimate_small_frame() -> None:
    body = b'{"ok": true}'
    stream = io.BytesIO(_header(len(body)) + body)
    assert parent_read_frame(stream) == body


def test_parent_accepts_boundary_size_at_cap() -> None:
    # Exactly at the cap is allowed; only strictly greater is rejected.
    cap_size = 64 * 1024 * 1024
    stream = io.BytesIO(_header(cap_size) + b"x" * cap_size)
    out = parent_read_frame(stream)
    assert out is not None
    assert len(out) == cap_size


def test_child_treats_oversize_header_as_eof() -> None:
    stream = io.BytesIO(_header(2**31))
    assert child_read_frame(stream) is None


def test_child_accepts_legitimate_frame() -> None:
    body = b"hello"
    stream = io.BytesIO(_header(len(body)) + body)
    assert child_read_frame(stream) == body
