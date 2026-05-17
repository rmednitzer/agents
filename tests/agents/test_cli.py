"""Tests for the agents CLI (BL-020, BL-021, BL-022)."""

from __future__ import annotations

import json
from typing import Any

import pytest

import agents.cli as cli
from agents.cli import build_parser, main


def test_parser_requires_a_command() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_bl020_workloads_list_includes_example(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["workloads", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "_example" in out
    assert "0.1.0" in out


def test_bl022_skills_list_grouped_by_lane(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["skills", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[documentation]" in out
    assert "example" in out


def test_bl021_run_example_passes(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["run", "_example", "# Title\n\nclean body\n"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["workload"] == "_example"
    assert payload["result"]["passed"] is True
    assert payload["dispatch"] is None  # _example declares no dispatcher


def test_bl021_run_example_reports_findings(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["run", "_example", "no heading at all"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["result"]["passed"] is False
    rules = {f["rule"] for f in payload["result"]["findings"]}
    assert "h1-required" in rules


def test_bl021_run_unknown_workload_errors(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["run", "_nope_xyz", "q"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_workloads_list_resilient_to_import_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A workload that raises a real ImportError is reported, not fatal."""
    real_load = cli.load_workload
    monkeypatch.setattr(cli, "_discover_workload_names", lambda: ["good", "broken"])

    def _fake_load(name: str, *, registry: Any) -> Any:
        if name == "broken":
            raise ImportError("No module named 'missing_dep'")
        return real_load("_example", registry=registry)

    monkeypatch.setattr(cli, "load_workload", _fake_load)
    rc = main(["workloads", "list"])
    out = capsys.readouterr()
    assert rc == 1  # a failure occurred...
    assert "_example" in out.out  # ...but the good one still listed
    assert "broken" in out.err
    assert "missing_dep" in out.err


def test_run_main_not_cli_callable_is_handled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A main() that needs extra args yields a clean error, not a traceback."""

    async def _bad_main(a: str, b: str) -> str:  # requires two positionals
        return a + b

    class _LW:
        manifest = type("M", (), {"dispatcher": None, "skills": []})()
        main = staticmethod(_bad_main)

    monkeypatch.setattr(cli, "load_workload", lambda name, *, registry: _LW())
    rc = main(["run", "x", "q"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_run_does_not_mask_genuine_typeerror_in_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TypeError raised *inside* main() must surface, not be relabelled."""

    async def _buggy_main(q: str) -> str:  # valid signature...
        raise TypeError("genuine bug in workload body")  # ...real bug

    class _LW:
        manifest = type("M", (), {"dispatcher": None, "skills": []})()
        main = staticmethod(_buggy_main)

    monkeypatch.setattr(cli, "load_workload", lambda name, *, registry: _LW())
    with pytest.raises(TypeError, match="genuine bug in workload body"):
        main(["run", "x", "q"])


# --- BL-161 / BL-125 CLI additions ------------------------------------


def test_run_json_flag_emits_compact(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["run", "_example", "# T\n\nok\n", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "\n  " not in out  # compact: no 2-space indentation
    assert json.loads(out)["workload"] == "_example"


def test_run_honours_model_free_manifest_dispatcher(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A manifest 'embedding' dispatcher is honoured model-free (BL-161)."""
    from skills.types import Skill, SkillManifest

    reg = cli.SkillRegistry()
    reg.add(
        Skill(
            manifest=SkillManifest(name="weather", description="rain sun forecast wind"),
            path=cli.Path("."),
        )
    )

    async def _main(q: str) -> str:
        return q

    class _LW:
        manifest = type("M", (), {"dispatcher": "embedding", "skills": ["weather"], "name": "wl"})()
        main = staticmethod(_main)

    monkeypatch.setattr(cli, "load_workload", lambda name, *, registry: _LW())
    monkeypatch.setattr(cli, "_skill_registry", lambda: reg)
    rc = main(["run", "wl", "will it rain"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["dispatch"]["dispatcher"] == "embedding"


def test_run_clean_import_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A workload import failure is reported cleanly, not as a traceback."""

    def _boom(name: str, *, registry: Any) -> Any:
        raise ImportError("No module named 'missing_dep'")

    monkeypatch.setattr(cli, "load_workload", _boom)
    rc = main(["run", "wl", "q"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "failed to import" in err
    assert "missing_dep" in err


def test_skills_install_from_local(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "src"
    d = root / "myskill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: myskill\ndescription: A CLI-installed skill.\n---\nbody\n"
    )
    rc = main(
        [
            "skills",
            "install",
            "myskill",
            "--from",
            f"local:{root}",
            "--dest",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 0
    assert "installed myskill" in capsys.readouterr().out
    assert (tmp_path / "out" / "myskill" / "SKILL.md").is_file()


def test_skills_install_bad_source_spec(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["skills", "install", "x", "--from", "weird:thing"])
    assert rc == 1
    assert "must be" in capsys.readouterr().err


def test_run_does_not_mask_import_error_in_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ImportError raised *inside* main() surfaces, not relabelled.

    Same honest-triage contract as a genuine in-body TypeError; the
    prior blanket `except ImportError` in cmd_run masked it (Codex #7).
    """

    async def _buggy_main(q: str) -> str:
        import a_module_that_truly_does_not_exist_zzz  # noqa: F401

        return q

    class _LW:
        manifest = type("M", (), {"dispatcher": None, "skills": []})()
        main = staticmethod(_buggy_main)

    monkeypatch.setattr(cli, "load_workload", lambda name, *, registry: _LW())
    with pytest.raises(ModuleNotFoundError, match="a_module_that_truly_does_not_exist_zzz"):
        main(["run", "x", "q"])
