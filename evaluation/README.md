# evaluation

The behavioural regression gate (BL-130, ADR 0011).

Contract: the CI gates (ruff, mypy, pytest, coverage) verify code
shape, not agent behaviour, so dispatch routing quality and
contract-outcome quality could regress silently (`LIMITATIONS.md` L6).
This component measures both against fixed success criteria so a
regression is a number that moves, not an invisible behaviour change.

- `metrics.py`: pure ranking metrics (`precision_at_1`,
  `mean_reciprocal_rank`, `hit_rank`, `reciprocal_rank`).
- `dataset.py`: `DispatchGoldenSet` (loadable from JSON, reviewable in
  a diff) and `TrajectoryCase` (input payload plus the contract
  terminal outcome it must reach).
- `harness.py`: `evaluate_dispatch` (P@1 / MRR over a golden set) and
  `evaluate_trajectory` (expected vs actual contract terminal
  outcome).
- `data/skills_dispatch.json`: the in-tree golden set for the
  deterministic `KeywordDispatcher` over `skills/`.

Example:

```python
from evaluation import evaluate_dispatch, load_dispatch_golden
from skills.dispatchers.keyword import KeywordDispatcher
from skills.registry import SkillRegistry

registry = SkillRegistry.from_directory(Path("skills"))
golden = load_dispatch_golden("evaluation/data/skills_dispatch.json")
report = await evaluate_dispatch(KeywordDispatcher(registry), golden)
assert report.meets(min_p_at_1=1.0, min_mrr=1.0)
```

`scripts/eval.py` runs the dispatch gate and exits non-zero on a
metric below threshold; it is deterministic and network-free and runs
in the `ci-success` aggregate. The harness is also usable on an
LLM-backed dispatcher or a live runtime (it only needs the `Dispatcher`
/ `Runtime` Protocol), gated to skip without credentials, when the
live-model reference workload (`BL-120`) lands.
