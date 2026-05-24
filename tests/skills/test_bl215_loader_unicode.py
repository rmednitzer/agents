"""BL-215 (seventh audit, ADR 0017): parse_skill_md UnicodeDecodeError catch.

Class extension of BL-204 (RecursionError catch). The loader documented
``SkillLoadError`` for unreadable files but only ``OSError`` was caught;
a SKILL.md that is not valid UTF-8 (e.g. accidentally saved latin-1, or
a binary file masquerading as a skill) raised ``UnicodeDecodeError`` and
leaked it past the documented exception boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skills.errors import SkillLoadError
from skills.loader import _read_body_only, parse_skill_md


def test_parse_skill_md_translates_unicode_decode_error(tmp_path: Path) -> None:
    bad = tmp_path / "SKILL.md"
    # 0xFF, 0xFE is a UTF-16 BOM that is invalid as the first bytes of
    # a UTF-8 stream and triggers UnicodeDecodeError on decode.
    bad.write_bytes(b"\xff\xfe---\nname: x\n---\n")
    with pytest.raises(SkillLoadError, match="not valid UTF-8"):
        parse_skill_md(bad)


def test_parse_skill_md_translates_latin1_high_byte(tmp_path: Path) -> None:
    bad = tmp_path / "SKILL.md"
    # A latin-1 encoded "café" byte sequence: 0xe9 is invalid as a
    # continuation byte in UTF-8 at this position.
    bad.write_bytes(b"---\nname: caf\xe9\n---\nbody\n")
    with pytest.raises(SkillLoadError, match="not valid UTF-8"):
        parse_skill_md(bad)


def test_read_body_only_translates_unicode_decode_error(tmp_path: Path) -> None:
    # Skill.body() lazy loader has the same boundary contract.
    bad = tmp_path / "SKILL.md"
    bad.write_bytes(b"\xff\xfe---\nname: x\n---\n")
    with pytest.raises(SkillLoadError, match="not valid UTF-8"):
        _read_body_only(bad)


def test_parse_skill_md_still_loads_valid_utf8(tmp_path: Path) -> None:
    # Sanity: the catch did not turn a happy path into an error.
    good = tmp_path / "SKILL.md"
    good.write_text(
        "---\nname: t\ndescription: café utf-8\n---\nbody\n",
        encoding="utf-8",
    )
    manifest, body = parse_skill_md(good)
    assert manifest.name == "t"
    assert "café" in manifest.description
    assert body.strip() == "body"
