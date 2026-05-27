# 2026-05-27 assurance engagement

A senior-assurance-engineer audit and remediation pass over `rmednitzer/agents` against the merged state of `main` at session start. Evidence-tagged per the engagement protocol (`[V]` verified this session, `[I]` inferred from premises, `[S]` speculative, `[?]` unknown, `[U]` user-provided). Confidence on the {50, 70, 80, 90} ladder where useful.

## 1. Scope

| Field | Value |
|---|---|
| Repository | `rmednitzer/agents` (Apache-2.0; Python 3.12+) |
| Engagement date | 2026-05-27 (UTC) |
| Branches touched (this session) | `claude/wizardly-faraday-ZwyQQ` (twice; both pushes merged and the branch was deleted upstream by GitHub's merge-and-delete setting) |
| Starting `main` SHA | `b597320` (`audit: tenth code audit, additive hardening (ADR 0020, BL-226 / BL-227) (#79)`) |
| Ending `main` SHA | `b3a6024` (`chore(deps): refresh uv.lock after the Dependabot specifier wave (#81)`) |
| Authorization scope (chosen at engagement start) | Standard execution: open PRs from the agent branch for approved Phase-1 items; patch/minor dep bumps in-scope; stop-and-confirm before governance artifacts (policies, controls, evidence, ADRs), CI / CODEOWNERS / branch-protection / secrets, breaking changes, schema migrations, dep majors, force-push / history-rewrite, or any diff exceeding 500 lines or 20 files |
| PRs opened this session | 2 |
| PRs merged this session | 2 |
| PRs closed unmerged | 0 |
| Issues opened or closed | 0 |
| Force-push / history rewrite / branch deletion | none |

## 2. Phase 0 inventory snapshot and delta

### 2.1 Snapshot at engagement start (HEAD `b597320`)

| Item | Value |
|---|---|
| Python LOC | ~30.9k across 73 modules `[V]` |
| Total `.md`/`.toml`/`.yaml` LOC | ~39.6k `[V]` |
| ADRs | 0001-0020 (all Accepted, frozen) `[V]` |
| Living governance docs | `STATUS.md`, `LIMITATIONS.md`, `CHANGELOG.md`, `docs/backlog.md`, `docs/runbook.md` `[V]` |
| Backlog open items | 8 (`BL-113`, `BL-114`, `BL-120`, `BL-132`, `BL-135` in-progress, `BL-138`, `BL-155`, `BL-171`, `BL-179`) `[V]` |
| TODO/FIXME/XXX/HACK in source | 0 (`mktemp` template suffixes only) `[V]` |
| GitHub: open PRs | 0 `[V]` |
| GitHub: open issues | 0 `[V]` |
| Branches on remote | `main` only (every feature branch pruned post-merge) `[V]` |
| Commit signing | all 20 recent commits report `%G?=E` (signed; verification key absent in execution env) `[V]` |
| CI workflows | `ci.yml`, `codeql.yml`, `release.yml`, all `uses:` SHA-pinned with trailing version comment `[V]` |
| Dependabot | pip + github-actions, weekly Monday 06:00 UTC, grouped patch+minor `[V]` |
| REUSE compliance | 3.x via tree-wide `REUSE.toml`, 245/245 files `[V]` |

### 2.2 Green-gate baseline at engagement start

Ran locally against `b597320`; every gate passed without modification:

| Gate | Result |
|---|---|
| `uv run ruff check .` | clean `[V]` |
| `uv run ruff format --check .` | 177 files clean `[V]` |
| `uv run mypy agents harness memory workloads skills evaluation` | clean (73 files) `[V]` |
| `uv run pytest --cov-fail-under=94` (Python 3.12) | 1039 passed, 95.00% coverage `[V]` |
| `uv run python scripts/eval.py --min-p-at-1 1.0 --min-mrr 1.0` | PASS (P@1=1.0, MRR=1.0) `[V]` |
| `uv run python scripts/gen_schema.py --check` | silent `[V]` |
| `uvx reuse lint` | REUSE 3.3 compliant (245/245) `[V]` |
| `uvx --python 3.12 pip-audit --strict --ignore-vuln PYSEC-2025-183 ...` | no known vulnerabilities `[V]` |
| Secret-pattern grep over tracked text/code | only synthetic `AKIA…` strings in `tests/harness/test_redaction.py` (intentional fixtures) `[V]` |

### 2.3 Delta (snapshot to engagement end)

| Item | Delta |
|---|---|
| `main` advanced two PRs (`6fae057`, `b3a6024`) | `[V]` |
| Doc drift D1-D9 closed | `[V]` |
| Lockfile metadata + locked-version lag closed | `[V]` |
| Open backlog count | unchanged (8 open; all upstream-blocked or live-credential-dependent) `[V]` |
| ADR set | unchanged (0001-0020 still Accepted, frozen) `[V]` |
| L-entries in `LIMITATIONS.md` | unchanged count; L4 wording updated to reflect resolved `BL-150` `[V]` |
| New `audit/` tree | this report only `[V]` |

## 3. Phase 1 backlog disposition

The engagement did not pull from the L3 open backlog; the work this session addressed was outside the ID-tracked backlog (post-audit doc sweep, post-Dependabot lockfile refresh). Backlog rows are unchanged.

| Open backlog ID | Tier | Reason still open | Disposition |
|---|---|---|---|
| `BL-113` | 2 | OTel logs SDK upstream not GA `[V]` `docs/backlog.md:194` | deferred (upstream) |
| `BL-114` | 2 | PydanticAI pause/resume primitive not stable `[V]` `docs/backlog.md:195` | deferred (upstream) |
| `BL-120` | 1 | Needs funded provider API keys and credentialed CI gate `[V]` `docs/backlog.md:199` | deferred (live credentials) |
| `BL-132` / `BL-171` | 1 | Needs verified PydanticAI provider-cache API + live model `[V]` `docs/backlog.md:223, 276` | deferred (upstream + live) |
| `BL-135` | 2 | Size-bound half fully delivered via `BL-212`-`BL-214` / `BL-224` / `BL-225`; the long-horizon compaction/summarisation/tiering half remains `[V]` `docs/backlog.md:229` | deferred (design wave) |
| `BL-138` | 2 | Depends on `BL-113` `[V]` `docs/backlog.md:231` | deferred (upstream chain) |
| `BL-155` | 2 | Needs thread/process execution boundary `[V]` `docs/backlog.md:252` | deferred (design wave) |
| `BL-179` | 2 | Needs upstream PydanticAI partial-usage on the exception path `[V]` `docs/backlog.md:293` | deferred (upstream) |

No items moved from `[pending]` to `[in-progress]` or to `[resolved]` this session. Every open item is correctly classified per the runbook §4.1 dependency column.

## 4. Phase 2 audit findings

One drift class, one deferred dependency-hygiene class. Both fully addressed.

### 4.1 D1-D9: post-ADR-0020 documentation drift

| ID | File / anchor | Drift | Severity | Status | PR |
|---|---|---|---|---|---|
| D1 | `STATUS.md` header "Last reviewed" | dated `2026-05-24 (BL-224 …)` after `BL-225` + `BL-226`/`BL-227` landed | low | fixed | #80 |
| D2 | `STATUS.md` L3 Tier 0 row | claimed "commit-SHA pinning is the tracked remainder" after `BL-150` resolved (PR #66, 2026-05-25) | medium | fixed | #80 |
| D3 | `LIMITATIONS.md` header "Last reviewed" | dated `2026-05-24` after ADR 0020 landed | low | fixed | #80 |
| D4 | `LIMITATIONS.md` L4 ("Supply-chain attestation incomplete") | claimed actions were tag-pinned; tracking row carried resolved `BL-150` | medium | fixed | #80 |
| D5 | `SECURITY.md` "Supply chain" bullet | same `BL-150` / tag-pinned claim | medium | fixed | #80 |
| D6 | `README.md` Status paragraph | enumerated audit waves through ADR 0019 / `BL-223` only; missing `BL-224`, `BL-225`, ADR 0020 (`BL-226` / `BL-227`) | low | fixed | #80 |
| D7 | `docs/runbook.md` multiple sites (§1 step 5, §2 cadence list, §2.3 fault-class rows, §4.1 / §4.2 "ready" set, §8.1 / §8.2 "today" markers + ADR range) | every "today" marker pointed at ADR 0019; §4.1 backlog table still listed resolved `BL-150` | low | fixed | #80 |
| D8 | `docs/README.md` inline ADR enumeration | ended at ADR 0019 | low | fixed | #80 |
| D9 | `docs/releasing.md` "Tracking" section | cited `BL-150` as open remainder | low | fixed | #80 |

Severity rationale: all docs-only, no behavioural impact. Medium where the drift overstated an open risk (claiming actions were tag-pinned, claiming a resolved `BL` was open); a reader could plan against a false constraint. Low where the drift was a stale enumeration with no semantic mismatch.

### 4.2 D10: lockfile metadata + locked-version lag

| ID | File / anchor | Drift | Severity | Status | PR |
|---|---|---|---|---|---|
| D10 | `uv.lock` `[package.metadata]` `requires-dist` block + `[[package]]` blocks for `anthropic` / `boto3` / `botocore` / `hf-xet` / `openai` / `pydantic-ai` / `pydantic-ai-slim` / `pydantic-evals` / `pydantic-graph` / `ruff` | the 2026-05-25 Dependabot wave (PRs #67, #68, #70, #72, #73, #74, #75) bumped `pyproject.toml` floor pins but the lockfile was not re-resolved, so CI on every merge since validated against the prior wave's locked versions, not the new floor-pin minimums | medium | fixed | #81 |

Severity rationale: medium because CI was effectively validating an older dependency surface than `pyproject.toml` declared. No vulnerability was masked (`pip-audit` was clean on both surfaces) and no test broke after the refresh, but the gate was less truthful than declared.

### 4.3 Other dimensions (no findings)

| Dimension | Pass / Fail | Notes |
|---|---|---|
| Correctness (type safety, concurrency, error handling, boundary contracts) | pass | mypy strict clean; the 10 prior code audits (ADRs 0009, 0010, 0011, 0013, 0015, 0017, 0018, 0019, 0020) have walked every dimension repeatedly; no new finding in this pass |
| Security (CVEs, secrets, authn/authz, crypto, injection surfaces, container hardening, OWASP ASVS L1 / API Top 10 / LLM Top 10) | pass | `pip-audit` clean; CodeQL security-extended runs on push, PR, and weekly; no secret material in tracked text or grepped history; `SECURITY.md` covers untrusted-content posture, prompt injection, and every load surface |
| Supply chain (pinning, provenance, SBOM, SLSA, signing) | pass on `BL-150` (now closed); `BL-151` (signed publish-to-index, full SLSA Build L2+) remains the open remainder, tracked in `LIMITATIONS.md` L1 / L4 | release workflow emits CycloneDX SBOM and attests build provenance |
| Reliability (idempotency, retry/backoff, timeouts, resource limits, graceful degradation, observability) | pass | the ADR 0010 `RetryPolicy` + circuit breaker covers the runtime adapter boundary; the audit-sink fan-out has BL-223/BL-227 per-leg containment; budgets are enforced at every boundary |
| Maintainability (cohesion, dead code, docstrings, ADR coverage, configuration hygiene) | pass | every public function typed; every component has a README; every cross-cutting decision has an ADR; no dead code identified |
| Governance and compliance (license, headers, audit logs, retention, data subject rights, certification scope) | pass | Apache-2.0; REUSE 3.x compliant; the framework is infrastructure, not a data processor; no certification scope claimed |

## 5. Phase 3 cross-check map

Standards consulted this session, by claim:

| Claim | Source | Tag |
|---|---|---|
| The per-`.md` sweep is a per-audit obligation | `docs/runbook.md` §8 | `[V]` |
| Style: no em-dashes, no `--` prose punctuation outside code spans | `CLAUDE.md` "Conventions / Documentation style" + `docs/runbook.md` §8.7 | `[V]` |
| ADR immutability: an Accepted ADR is frozen; errata go in the next ADR | `docs/adr/README.md` header + `CLAUDE.md` "Conventions / ADR immutability" | `[V]` |
| ISO 8601 dates, 24h UTC | `CLAUDE.md` "Conventions / Dates and units" | `[V]` |
| REUSE 3.x covers new files via tree-wide `REUSE.toml` (no per-file header required) | `REUSE.toml` `path = "**"`, `precedence = "aggregate"` | `[V]` |
| Conventional Commits + `docs:` / `chore(deps):` prefixes | repo history (PR #57 `481a3ae`, PR #55 `b8da7e5`, PR #70, PR #72, etc.) | `[V]` |
| Pre-PR green gate is `make check` plus `scripts/eval.py` plus `gen_schema.py --check` plus `uvx reuse lint` plus `pip-audit` | `Makefile` + `.github/workflows/ci.yml` + `docs/runbook.md` §5.1 | `[V]` |
| Dependabot floor-pin bumps already authorise the locked-version moves the refresh applies | the merged PRs #67, #68, #70, #72, #73, #74, #75 themselves | `[V]` |
| The PydanticAI Protocol decouples the framework from upstream-minor churn (1.99 → 1.103) | `ADR 0001` + `LIMITATIONS.md` L8; full test suite is the live check | `[V]` |

External standards mentioned in the engagement protocol (OWASP ASVS v4.0.3, OWASP API Top 10 2023, OWASP LLM Top 10, NIST SSDF SP 800-218, NIST SP 800-53 / 800-161, SLSA v1.2, CIS Benchmarks) were not the binding source for any decision this session, because the work delivered was documentation drift correction and lockfile resolution within scope already authorised by the repo's own runbook and prior PRs. The repo's `SECURITY.md` already maps to OWASP / ASVS-style hardening categories. `[I]` @80.

## 6. Phase 4 validation suite changes

No gates added or strengthened this session. Every gate was already in place at engagement start (ADR 0008 / 0010 / 0011 / 0013), passed at start, and passed at end against both PRs' diffs.

One recommendation surfaces from D10:

| Recommendation | Rationale |
|---|---|
| Add a `uv lock --check` (or equivalent freshness assertion) to the `dependency-audit` CI job, or extend the existing job to fail when `uv.lock` is not in agreement with `pyproject.toml` | A Dependabot PR that bumps a `pyproject.toml` specifier without re-resolving the lockfile would then fail CI, surfacing D10-class drift at the PR boundary rather than letting it accumulate over a wave |

Not landed this session (out of scope for the two PRs taken). Suggested as a follow-up; would qualify as a Phase 5 item if the user authorises.

## 7. Phase 5 execution log

| # | Commit | Title | Diff | Gate | PR | Outcome |
|---|---|---|---|---|---|---|
| 1 | `806e981` -> merged as `6fae057` | `docs: post-ADR-0020 sweep (runbook §8)` | 8 files, +60 / -33 (docs only) | full green gate ran locally pre-push: ruff (clean), ruff format (177 files), mypy strict (73 files clean), pytest 1039 passed @ 94.98% cov, eval P@1=MRR=1.0, gen_schema --check (silent), reuse lint (245/245), pip-audit (no vulns). CI then ran the same gates on the PR and merged green. | [#80](https://github.com/rmednitzer/agents/pull/80) | merged |
| 2 | `99b9e72` -> merged as `b3a6024` | `chore(deps): refresh uv.lock after the Dependabot specifier wave` | 1 file (`uv.lock`), +78 / -77 | full green gate ran locally pre-push against the refreshed lockfile: same shape as #80 plus pytest 1039 passed @ 95.00% cov. PR merged green. | [#81](https://github.com/rmednitzer/agents/pull/81) | merged |

Per-merge state mutation outside the diff: none. No CI config changed, no `CODEOWNERS` or branch-protection changed, no governance artifact altered beyond the in-scope documentation drift in #80 (`SECURITY.md`, `docs/releasing.md`, both edited inside the pre-declared governance pause-and-confirm gate per the engagement protocol §7.5).

## 8. Outstanding risks and recommended next steps

### 8.1 Open risks (unchanged from engagement start; tracked in repo)

| ID | Risk | Where tracked |
|---|---|---|
| `BL-120` | No live-model reference workload exercises the wired runtime end-to-end | `docs/backlog.md`, `LIMITATIONS.md` L2 |
| `BL-114` | Approval-pause resume is a replay against the model; non-idempotent tool calls re-execute | `LIMITATIONS.md` L10 |
| `BL-135` (compaction half) | Long-horizon workloads still grow unbounded by compaction even with the size-bound sweeper | `LIMITATIONS.md` L5 |
| `BL-155` | Wall-clock budget watchdog preempts only at an await boundary | `LIMITATIONS.md` L11 |
| `BL-151` | No signed publish-to-index; not yet SLSA Build L2+ | `LIMITATIONS.md` L1 / L4, `docs/releasing.md` |
| L8 (PydanticAI coupling) | The runtime adapter targets a pre-1.0 library; a breaking upstream change may require an adapter update | `LIMITATIONS.md` L8 |

Every open risk is upstream-blocked, live-credential-dependent, or a significant new design wave, per the engagement-end disposition in §3 above.

### 8.2 Recommended next steps

1. **Lockfile freshness gate in CI.** Add `uv lock --check` (or equivalent) to the `dependency-audit` job so a future Dependabot PR that bumps `pyproject.toml` without re-resolving the lockfile fails at the PR boundary. Closes the D10 class structurally. Trivial PR, no behavioural impact. `[I]` @90.
2. **Dependabot grouping by ecosystem extra.** The current Dependabot config groups all pip minor/patch into one PR. With BL-186/BL-187 introducing optional Anthropic / OpenAI batch capabilities, a per-extra grouping (`anthropic`, `openai`, `aws`, `redis`, `crypto`, `otel`) would make each PR's blast radius easier to reason about. Optional; current grouping is acceptable. `[S]` @60.
3. **`BL-120` reference workload.** The single highest-leverage open item per `STATUS.md` and the runbook §4.1 "ready" set. Lands the live-model adapter exercise that retires `LIMITATIONS.md` L2 and unblocks `BL-132` / `BL-138` / `BL-171` validation. Needs API keys + a credentialed CI gate; out of scope for an automated assurance pass.
4. **ADR 0021 (the eleventh code audit).** The runbook §2 audit-cadence enumeration now advances to `next audit slot is ADR 0021`. Schedule per the §9 cycle calendar (per-quarter, or after the next dependency-bump cluster). Not yet due.

## 9. Provenance

| Field | Value |
|---|---|
| Authored by | Claude (`claude-opus-4-7[1m]`), Anthropic Claude Code on the web |
| Authored at | 2026-05-27 (UTC) |
| Session URL | https://claude.ai/code/session_014gtkUzCDRv8mX4Nix47WGY |
| Evidence artifacts | inline above; no separate `audit/2026-05-27/evidence/` tree was produced because every gate output cited was a deterministic one-line summary reproducible by re-running the command at HEAD `b3a6024` against the in-tree `pyproject.toml` / `uv.lock` / `Makefile` |
| Reproducibility | `git checkout b3a6024 && uv sync --all-extras && make check && uv run python scripts/eval.py --min-p-at-1 1.0 --min-mrr 1.0 && uv run python scripts/gen_schema.py --check && uvx reuse lint && uv export --frozen --all-extras --no-emit-project --format requirements-txt -o /tmp/audit-requirements.txt && uvx --python 3.12 pip-audit --strict --progress-spinner=off --ignore-vuln PYSEC-2025-183 -r /tmp/audit-requirements.txt` reproduces the green-gate observations |
