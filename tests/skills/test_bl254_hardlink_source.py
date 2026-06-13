"""BL-254 (fifteenth audit): LocalSkillSource refuses a hardlink.

`LocalSkillSource.fetch` refuses a symlink in the source tree so a
crafted local mirror cannot copy a secret's CONTENTS into the installed
bundle. A hardlink achieves the same exfiltration (it is a second
directory entry for an inode that may live outside the source tree) but
is a regular file by ``is_symlink()`` / ``is_file()``, so it slipped the
symlink refusal. The fix refuses any regular file with ``st_nlink > 1``,
the conservative stance matching the symlink refusal.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from skills.errors import SkillLoadError
from skills.sources import LocalSkillSource

_SKILL_MD = "---\nname: myskill\ndescription: a test skill bundle\n---\n\nbody\n"


def _make_source(root: Path) -> Path:
    skill = root / "myskill"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text(_SKILL_MD)
    return skill


def test_localskillsource_refuses_hardlink_to_external_file(tmp_path: Path) -> None:
    src_root = tmp_path / "src"
    skill = _make_source(src_root)
    # A secret outside the skill tree, hardlinked into references/ (same
    # filesystem as tmp_path, so os.link succeeds).
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET CONTENT")
    os.link(secret, skill / "references" / "leak.txt")

    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(SkillLoadError, match="hardlink"):
        LocalSkillSource(src_root).fetch("myskill", dest)


def test_localskillsource_accepts_plain_regular_files(tmp_path: Path) -> None:
    # The guard must not reject an ordinary single-link file.
    src_root = tmp_path / "src"
    skill = _make_source(src_root)
    (skill / "references" / "ok.txt").write_text("fine")

    dest = tmp_path / "dest"
    dest.mkdir()
    out = LocalSkillSource(src_root).fetch("myskill", dest)
    assert (out / "SKILL.md").is_file()
    assert (out / "references" / "ok.txt").read_text() == "fine"
