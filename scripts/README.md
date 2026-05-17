# scripts/

Operational and developer scripts. Bash and Python. Each script has a header comment explaining purpose and usage.

- `gen_schema.py`: regenerate `docs/schema/*.json` from the Pydantic
  models (`make schema`). `--check` is side-effect-free and is run by
  the test suite as a drift guard.
- `eval.py`: the `BL-130` behavioural regression gate. Builds a
  `SkillRegistry` from `skills/`, runs the deterministic
  `KeywordDispatcher` over `evaluation/data/skills_dispatch.json`,
  prints P@1 / MRR, and exits non-zero below `--min-p-at-1` /
  `--min-mrr`. Network-free; the CI `evaluation` job runs it in the
  `ci-success` aggregate.
