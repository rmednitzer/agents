"""Operator CLI implementation (BL-020, BL-021, BL-022).

Kept import-light and side-effect-free at module import so `python -m
agents --help` is cheap. Discovery is filesystem-based over the
``workloads`` and ``skills`` package directories; ``run`` leans on the
workload's own entry point and ``run_under_contract`` rather than
re-implementing execution.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

import skills as skills_pkg
import workloads as workloads_pkg
from skills.dispatchers import KeywordDispatcher
from skills.registry import SkillRegistry
from workloads.errors import WorkloadError
from workloads.loader import load_workload

__all__ = ["build_parser", "main"]


def _workloads_root() -> Path:
    assert workloads_pkg.__file__ is not None
    return Path(workloads_pkg.__file__).parent


def _skills_root() -> Path:
    assert skills_pkg.__file__ is not None
    return Path(skills_pkg.__file__).parent


def _discover_workload_names() -> list[str]:
    """Every immediate subdirectory of workloads/ that has a manifest."""
    root = _workloads_root()
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and (entry / "manifest.yaml").is_file()
    )


def _skill_registry() -> SkillRegistry:
    return SkillRegistry.from_directory(_skills_root())


def cmd_workloads_list(args: argparse.Namespace) -> int:
    """BL-020: print every loadable workload's name, version, description."""
    registry = _skill_registry()
    failures = 0
    for name in _discover_workload_names():
        try:
            lw = load_workload(name, registry=registry)
        except Exception as exc:
            # load_workload now propagates a real ImportError (a
            # workload with a broken dependency) instead of masking it;
            # listing must stay resilient -- report this one as
            # unloadable and continue with the rest.
            failures += 1
            print(f"{name}\t<unloadable: {exc!r}>", file=sys.stderr)
            continue
        desc = " ".join(lw.manifest.description.split())
        print(f"{lw.manifest.name}\t{lw.manifest.version}\t{desc}")
    return 1 if failures else 0


def cmd_skills_list(args: argparse.Namespace) -> int:
    """BL-022: print every skill in skills/, grouped by lane."""
    registry = _skill_registry()
    by_lane: dict[str, list[Any]] = {}
    for skill in registry.all():
        by_lane.setdefault(skill.lane or "(no lane)", []).append(skill)
    for lane in sorted(by_lane):
        print(f"[{lane}]")
        for skill in sorted(by_lane[lane], key=lambda s: s.name):
            desc = " ".join(skill.description.split())
            print(f"  {skill.name}\t{desc}")
    return 0


async def _run_workload(name: str, query: str) -> dict[str, Any]:
    registry = _skill_registry()
    lw = load_workload(name, registry=registry)

    dispatch: dict[str, Any] | None = None
    if lw.manifest.dispatcher and lw.manifest.skills:
        # Deterministic, model-free routing so the CLI works without
        # API keys. LLM/skill-based dispatch needs programmatic wiring.
        matches = await KeywordDispatcher(registry).dispatch(query, limit=1)
        if matches:
            top = matches[0]
            dispatch = {
                "skill": top.skill_name,
                "confidence": top.confidence,
                "rationale": top.rationale,
            }

    if lw.main is None:
        raise WorkloadError(
            f"workload {name!r} has no __main__.main; invoke it "
            "programmatically via run_under_contract"
        )
    # Validate the CLI calling convention up front by binding the single
    # positional arg, NOT by catching TypeError around the call -- the
    # latter would mask a genuine TypeError raised inside the workload
    # body and mislabel a real bug as a signature error.
    try:
        inspect.signature(lw.main).bind(query)
    except TypeError as exc:
        raise WorkloadError(
            f"workload {name!r} main() is not CLI-callable as main(query): {exc}"
        ) from exc
    result = await lw.main(query)
    payload = result.model_dump() if isinstance(result, BaseModel) else result
    return {"workload": name, "dispatch": dispatch, "result": payload}


def cmd_run(args: argparse.Namespace) -> int:
    """BL-021: load a workload, optionally dispatch, run under contract."""
    try:
        out = asyncio.run(_run_workload(args.workload, args.query))
    except WorkloadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    wl = sub.add_parser("workloads", help="workload bundle commands")
    wl_sub = wl.add_subparsers(dest="subcommand", required=True)
    wl_list = wl_sub.add_parser("list", help="list loadable workloads")
    wl_list.set_defaults(func=cmd_workloads_list)

    sk = sub.add_parser("skills", help="skill commands")
    sk_sub = sk.add_subparsers(dest="subcommand", required=True)
    sk_list = sk_sub.add_parser("list", help="list skills, grouped by lane")
    sk_list.set_defaults(func=cmd_skills_list)

    run = sub.add_parser("run", help="run a workload against a query")
    run.add_argument("workload", help="workload package name")
    run.add_argument("query", help="query/content passed to the workload")
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Any = args.func
    result = func(args)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
