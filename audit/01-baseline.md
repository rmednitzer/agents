# 2026-06-12 full pass: Phase 1 validation baseline

Regression reference for every later change in this pass. All
commands were run in this session at commit `70be1436` on a clean
clone, in the order listed. The environment is the managed remote
execution container described in `audit/00-inventory.md` section 6.

## 1. Clean build

| Step | Command | Outcome |
|---|---|---|
| Dependency sync | `uv sync --all-extras` | exit 0 (all extras + dev group installed into `.venv`, Python 3.12.3) |

## 2. Green-gate results (CI parity)

The local run reproduces every blocking job in
`.github/workflows/ci.yml`:

| Gate | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | clean ("All checks passed!") |
| Format | `uv run ruff format --check .` | clean ("186 files already formatted") |
| Types | `uv run mypy agents harness memory workloads skills evaluation` | clean ("Success: no issues found in 75 source files") |
| Tests + coverage | `uv run pytest --cov=agents --cov=harness --cov=memory --cov=skills --cov=workloads --cov=evaluation --cov-report=term --cov-fail-under=94 -q` | **1170 passed**, 0 failed, 0 skipped, 63.48 s; **total coverage 95.25 %** (gate: 94 %) |
| Evaluation gate | `uv run python scripts/eval.py --min-p-at-1 1.0 --min-mrr 1.0` | PASS (P@1 = 1.000, MRR = 1.000, n = 6) |
| Schema drift | `uv run python scripts/gen_schema.py --check` | silent pass (exit 0) |
| REUSE | `uvx reuse lint` | compliant with REUSE 3.3 (264/264 files carry copyright + license) |
| Lockfile freshness | `uv lock --check` | pass ("Resolved 173 packages"; lockfile in sync with `pyproject.toml`) |
| Dependency audit (CI parity) | `uv export --frozen --all-extras --no-emit-project --format requirements-txt -o /tmp/audit-requirements.txt && uvx --python 3.12 pip-audit --strict --progress-spinner=off --ignore-vuln PYSEC-2025-183 -r /tmp/audit-requirements.txt` | exit 0, "No known vulnerabilities found" |
| Dependency audit (unsuppressed) | same command **without** `--ignore-vuln PYSEC-2025-183` | exit 0, "No known vulnerabilities found" (this is finding F-1: the suppression no longer suppresses anything) |

## 3. Coverage

Coverage tooling exists and is CI-enforced (`--cov-fail-under=94`).
Measured this session: 95.25 % total (5575 statements, 265 missed).
`skills/_executor_child.py` is excluded by `[tool.coverage.run] omit`
with an in-file rationale (subprocess entry point, exercised
functionally by the BL-133 tests, invisible to the parent's
pytest-cov).

## 4. Flaky candidates

None observed. Single run only (n = 1), so this baseline cannot rule
out low-frequency flakes; the suite is deterministic by design
(`TestModel`/`FunctionModel` PydanticAI doubles, `fakeredis`, `moto`,
no network, no sleeps in source: `grep -rn "time.sleep" <source dirs>`
returned nothing).

## 5. CI drift check (local vs `.github/workflows/ci.yml`)

- Every blocking CI gate reproduces locally with the same commands
  and passes (table above).
- The `test` job's Python 3.13 matrix leg was not reproduced locally
  (only 3.12.3 was exercised); `[UNVERIFIED]` locally, CI covers it.
- `make check` runs pytest without the coverage flags and omits
  `ruff format --check`, `reuse lint`, the eval gate, pip-audit, and
  the schema check; this is documented behaviour
  (`docs/runbook.md` section 2.2 lists the full set explicitly), not
  silent drift.
- Two CI gaps are recorded as findings rather than drift:
  the stale `--ignore-vuln PYSEC-2025-183` suppression (F-1) and the
  absence of a lockfile-freshness assertion in the
  `dependency-audit` job (F-2, `uv export --frozen` uses the lockfile
  as-is and does not verify it against `pyproject.toml`). See
  `audit/02-security-findings.md`.

## 6. Baseline summary

The repository is fully green at `70be1436` on every gate it declares,
plus two gates it does not yet declare (`uv lock --check`, the
unsuppressed pip-audit). This table is the comparison reference for
the post-fix state in `audit/03-final-report.md`.
