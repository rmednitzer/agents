# 2026-06-12 full pass: Phase 0 inventory

Read-only recon snapshot for the 2026-06-12 audit, validation,
hardening, and documentation pass. Baseline commit: `70be1436`
(`Memory compaction, summarisation, and hot/cold tiering (ADR 0024,
BL-234 / BL-235) (#110)`), even with `origin/main` at session start
(verified: `git fetch origin main && git rev-parse origin/main`).
Every figure below was produced by a command run in this session; the
command is cited inline.

## 1. Identity and scope

| Field | Value |
|---|---|
| Repository | `rmednitzer/agents`, Apache-2.0 (`LICENSE`, `NOTICE`, REUSE 3.x via `REUSE.toml`) |
| Purpose | Infrastructure for agentic workloads: harness (contracts, budgets, guards), memory backends, skill bundles, workload loader, evaluation gate |
| Language | Python, `requires-python = ">=3.12"` (`pyproject.toml`); CI matrix 3.12 + 3.13 |
| Build system | `hatchling` backend, `uv` package manager, `Makefile` targets (`make help`) |
| Version | `0.0.1`, Development Status 2 (Pre-Alpha); no tags, no published package (`STATUS.md` Release section) |
| Audit branch | `claude/sleepy-fermi-l2k07d` (session-pinned by the execution environment; the engagement protocol's `audit/2026-06-12-full-pass` name could not be used because pushes are restricted to the session branch) |

## 2. Component map

Counts from `find <dir> -name '*.py' | wc -l` and `find <dir> -name
'*.py' -exec cat {} + | wc -l` per top-level directory:

| Component | Files | Lines | Role |
|---|---|---|---|
| `agents/` | 3 | 303 | Operator CLI (`python -m agents`, console script `agents = agents.cli:main`) |
| `harness/` | 20 | 4228 | Contracts, enforcement, runtime adapter (PydanticAI), budgets, events, retry, provenance, provider batch capabilities |
| `memory/` | 18 | 6055 | Namespace-bound `MemoryStore` + adapters (inmemory, sqlite, redis, s3, dynamodb), encryption, ACL, semantic, compaction, tiering |
| `skills/` | 23 | 3348 | Skill loader, registry, dispatchers, install sources, contract execution (in-process + subprocess) |
| `workloads/` | 7 | 818 | Workload loader + `_example` reference workload |
| `evaluation/` | 4 | 385 | Dispatch P@1/MRR + trajectory regression gate |
| `scripts/` | 3 | 342 | `eval.py` (CI gate), `gen_schema.py` (JSON Schema), `check_run_records.py` (provenance gate) |
| `tests/` | 108 | 18286 | Mirrors source layout; 1032 test functions (AST count, see Phase 3) |

Totals: 186 `.py` files, 58 `.md` files (`find . -name '*.md' -not
-path './.git/*' | wc -l`). No Dockerfile, docker-compose, Terraform,
or Kubernetes manifests anywhere in the tree (`find -maxdepth 2` over
those name patterns returned nothing): there is no container or IaC
surface to audit.

## 3. Entry points and external input surfaces

| Surface | Location | Validation posture (assessed in Phase 2) |
|---|---|---|
| CLI | `agents/cli.py` via `python -m agents` / console script | argparse; workload + skill names resolved through validated loaders |
| Workload manifests | `workloads/loader.py` (`manifest.yaml`) | `yaml.safe_load` + strict Pydantic models; JSON Schema generated and drift-gated |
| Skill bundles | `skills/loader.py`, `skills/sources.py` | `yaml.safe_load` frontmatter; bounded, symlink-safe tar extraction; optional sha256 pin; `allow_contract=False` default |
| Subprocess IPC | `skills/execution.py`, `skills/_executor_child.py` | Length-prefixed frames, 64 MiB cap, child-to-parent JSON only; pickle only parent-to-child (trusted direction) |
| Env vars | 6 sites (`grep -rn "os.environ|getenv"` over source): `memory/encryption.py:197` (key provider), `skills/execution.py:259`, `skills/_executor_child.py:162-165` (rlimits, parent-set) | Key material read by explicit provider; rlimit values set by the parent process, not attacker-controlled |
| Network clients | `harness/anthropic_api.py`, `harness/openai_api.py` (lazy SDK), memory backends (redis/boto3) | SDKs injected via Protocol; no in-tree network listener exists |
| External metadata | S3 user metadata, DynamoDB attributes | `_safe_float` / `_safe_int` (BL-226), server-validated `N` type (BL-230) |

## 4. Dependency graph summary

From `pyproject.toml` and `uv lock --check` (resolved 173 packages):

- Direct base dependencies: 4 (`pydantic-ai>=1.104.0`,
  `pydantic>=2.13.4`, `logfire>=4.34.0`, `pyyaml>=6.0.3`).
  Finding F-4: `logfire` has zero references in source, tests, and
  docs, and `uv pip show logfire` reports `Required-by: agents` only
  (see `audit/02-security-findings.md`).
- Optional extras: `redis`, `aws` (boto3), `otel`, `crypto`
  (cryptography), `anthropic`, `openai`, plus a `dev` group (pytest,
  ruff, mypy, fakeredis, moto, type stubs).
- Lockfile: `uv.lock`, 173 packages, fully hash-pinned (1809
  `sha256:` lines, `grep -c 'sha256:' uv.lock`), fresh against
  `pyproject.toml` (`uv lock --check` passed 2026-06-12).
- Dependency automation: Renovate (`renovate.json5`):
  `config:best-practices`, weekly schedule, grouped non-major updates,
  Monday lockfile maintenance, OSV vulnerability alerts enabled.

## 5. CI and repository governance

From `.github/workflows/` (read in full this session):

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | push/PR to main | `lint` (ruff check + format check + `uvx reuse lint`), `type-check` (mypy strict), `test` (pytest, 3.12 + 3.13 matrix, `--cov-fail-under=94`), `dependency-audit` (pip-audit over the exported lockfile), `evaluation` (`scripts/eval.py`, P@1 = MRR = 1.0), `ci-success` aggregate |
| `codeql.yml` | push/PR/weekly cron | CodeQL `security-extended`, Python |
| `release.yml` | `v*` tags | full gate, build, CycloneDX SBOM, build-provenance attestation, GitHub release |

- Workflow permissions: top-level `permissions: contents: read` in all
  three; job-level escalation only where needed (`security-events:
  write` in CodeQL, `contents: write` + `id-token: write` +
  `attestations: write` in the release build job).
- Action pinning: 18 `uses:` references across the three workflows,
  0 without a 40-hex commit SHA pin
  (`grep -h "uses:" .github/workflows/*.yml | grep -vc "@[0-9a-f]\{40\}"`
  returned 0).
- `CODEOWNERS`: `* @rmednitzer`. Issue templates and a PR template
  exist under `.github/`.

## 6. Toolchain available in this environment

Recorded from version commands run this session:

| Tool | Version |
|---|---|
| uv | 0.8.17 |
| Python (project venv) | 3.12.3 (`uv run python --version`; system `python3` is 3.11.15 and is not used by the project) |
| ruff | 0.15.16 |
| mypy | 2.1.0 (compiled) |
| pytest | 9.0.3 |
| pip-audit (via `uvx`) | 2.10.1 |
| gitleaks | system binary at `/usr/bin/gitleaks` (`gitleaks version` prints no number in this build) |
| semgrep, trufflehog, bandit | not installed (`which` returned nothing); CodeQL `security-extended` in CI is the compensating SAST control |

## 7. Document and decision inventory

- 24 ADRs (`docs/adr/0001` to `0024`), all status Accepted; index in
  `docs/adr/README.md`.
- Living docs: `STATUS.md`, `LIMITATIONS.md`, `CHANGELOG.md`,
  `docs/backlog.md` (canonical line-item tracker), `docs/runbook.md`.
- Open backlog at session start: 8 items, all `[pending]` and
  upstream-blocked, credential-dependent, or design-wave scoped
  (`BL-113`, `BL-114`, `BL-120`, `BL-132`, `BL-138`, `BL-155`,
  `BL-171`, `BL-179`; `grep -n '\[pending\]\|\[in-progress\]'
  docs/backlog.md`).
- Prior assurance artifacts: 13 code audits (ADRs 0009 to 0023) plus
  the 2026-05-27 engagement record (`audit/2026-05-27-engagement.md`).
- The only code that has never been through an audit pass is the
  ADR 0024 wave (`memory/compaction.py`, `memory/tiering.py`, their
  tests): `git diff --stat d704f23..HEAD` shows nothing else changed
  since the thirteenth audit except docs and `uv.lock`. Those two
  modules were read line-by-line in Phase 3.
