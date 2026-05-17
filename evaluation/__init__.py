"""agents evaluation: measured dispatch + contract-trajectory quality.

The behavioural regression gate (BL-130). CI verifies code shape;
this verifies that routing quality (P@1 / MRR over a golden set) and
contract-outcome quality (a trajectory fixture) do not regress
silently (LIMITATIONS L6). Deterministic and network-free, so it runs
in CI via scripts/eval.py.
"""

from evaluation.dataset import (
    DispatchCase,
    DispatchGoldenSet,
    TrajectoryCase,
    TrajectoryOutcome,
    load_dispatch_golden,
)
from evaluation.harness import (
    CaseResult,
    DispatchReport,
    TrajectoryReport,
    TrajectoryResult,
    evaluate_dispatch,
    evaluate_trajectory,
)
from evaluation.metrics import (
    hit_rank,
    mean_reciprocal_rank,
    precision_at_1,
    reciprocal_rank,
)

__all__ = [
    "CaseResult",
    "DispatchCase",
    "DispatchGoldenSet",
    "DispatchReport",
    "TrajectoryCase",
    "TrajectoryOutcome",
    "TrajectoryReport",
    "TrajectoryResult",
    "evaluate_dispatch",
    "evaluate_trajectory",
    "hit_rank",
    "load_dispatch_golden",
    "mean_reciprocal_rank",
    "precision_at_1",
    "reciprocal_rank",
]
