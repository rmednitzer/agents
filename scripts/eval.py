#!/usr/bin/env python3
"""Run the dispatch evaluation and gate on P@1 / MRR (BL-130).

Purpose:
    Build a SkillRegistry from the in-tree ``skills/`` tree, run the
    deterministic KeywordDispatcher over the golden set in
    ``evaluation/data/skills_dispatch.json``, print the report, and
    exit non-zero if a metric falls below its threshold. This is the
    behavioural regression gate CI lacked (LIMITATIONS L6); it is
    deterministic and network-free, so it belongs in the ``ci-success``
    aggregate.

Usage:
    python scripts/eval.py
    python scripts/eval.py --min-p-at-1 1.0 --min-mrr 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Make the repo root importable when run as a bare script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evaluation.dataset import load_dispatch_golden  # noqa: E402
from evaluation.harness import evaluate_dispatch  # noqa: E402
from skills.dispatchers.keyword import KeywordDispatcher  # noqa: E402
from skills.registry import SkillRegistry  # noqa: E402

_GOLDEN = _REPO_ROOT / "evaluation" / "data" / "skills_dispatch.json"
_SKILLS = _REPO_ROOT / "skills"


async def _run(min_p_at_1: float, min_mrr: float) -> int:
    registry = SkillRegistry.from_directory(_SKILLS)
    golden = load_dispatch_golden(_GOLDEN)
    report = await evaluate_dispatch(KeywordDispatcher(registry), golden, limit=5)

    print(f"dispatch eval: {report.name}  (n={report.n})")
    print(f"  P@1 = {report.precision_at_1:.3f}  (min {min_p_at_1:.3f})")
    print(f"  MRR = {report.mrr:.3f}  (min {min_mrr:.3f})")
    for r in report.results:
        mark = "ok " if r.rank == 1 else "BAD"
        print(f"  [{mark}] rank={r.rank} {r.query!r} -> {list(r.predicted)}")

    if report.meets(min_p_at_1=min_p_at_1, min_mrr=min_mrr):
        print("PASS")
        return 0
    print("FAIL: a dispatch metric is below threshold (routing regression)")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch evaluation gate (BL-130)")
    parser.add_argument("--min-p-at-1", type=float, default=1.0)
    parser.add_argument("--min-mrr", type=float, default=1.0)
    args = parser.parse_args()
    return asyncio.run(_run(args.min_p_at_1, args.min_mrr))


if __name__ == "__main__":
    raise SystemExit(main())
