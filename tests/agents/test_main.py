"""Tests for the ``python -m agents`` entry point."""

from __future__ import annotations

import runpy

import pytest

import agents.cli as cli


def test_module_entrypoint_exits_with_cli_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "main", lambda: 7)
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("agents.__main__", run_name="__main__")
    assert excinfo.value.code == 7
