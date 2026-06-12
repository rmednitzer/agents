# 2026-06-12 full pass: Phase 8 final report

## Executive summary

A full audit, validation, hardening, and documentation pass over
`rmednitzer/agents` at `70be1436` (even with `origin/main` at session
start), run as the repository's fourteenth audit under an external
phased engagement protocol. The codebase entered the pass fully green
on every declared gate and left it fully green with three process
fixes, two comment-accuracy fixes, and a tree-wide documentation
sweep; no runtime defect was found in any swept dimension. The only
code never previously audited (the ADR 0024
`memory/compaction.py` / `memory/tiering.py` wave) was read line by
line and came back clean. The pass's substantive findings were all
one level above the code, in its gates and declarations: a pip-audit
suppression that had outlived its target, a missing
lockfile-freshness assertion that a prior engagement had recommended
and nobody landed, an unused direct dependency declaration, and
post-ADR-0024 documentation drift. Decisions recorded in
[ADR 0025](../docs/adr/0025-fourteenth-audit-full-pass.md).

## Baseline vs post-fix metrics

Both columns measured in this session with identical commands
(`audit/01-baseline.md` holds the baseline detail):

| Metric | Baseline (`70be1436`) | Post-fix (this branch) |
|---|---|---|
| Tests | 1170 passed, 0 failed, 0 skipped | 1170 passed, 0 failed, 0 skipped |
| Coverage | 95.25 % (gate 94 %) | 95.21 % (gate 94 %; the 2-statement delta against baseline is run-to-run variance on timing-dependent branches, observed across runs at identical source statement counts, 5575) |
| ruff check / format | clean / 186 files | clean / 186 files |
| mypy strict | clean (75 files) | clean (75 files) |
| Known vulnerabilities (pip-audit, strict) | 0 (but 1 stale suppression configured) | 0 (0 suppressions configured) |
| Lockfile freshness | fresh, unasserted by CI | fresh, CI-gated (`uv lock --check`) |
| Direct base dependencies | 4 (one unused) | 3 (all imported) |
| Eval gate | P@1 = MRR = 1.0 | P@1 = MRR = 1.0 |
| Schema drift / REUSE | clean / 264 files compliant | clean / compliant (new files auto-covered) |
| Secrets (gitleaks, tree + 50 commits) | 4 synthetic test fixtures only | unchanged (documented as F-3) |
| ADRs / docs currency | 24 ADRs; README + docs/README + runbook stale post-ADR-0024 | 25 ADRs; tree-wide consistent |

## Commits on this branch

| Commit | Rationale |
|---|---|
| `07b5865` docs(audit): 2026-06-12 full-pass evidence (phases 0-3) | Inventory, baseline, findings register; read-only evidence, no behaviour change |
| `b57f6bb` security(ci): drop the stale PYSEC-2025-183 pip-audit suppression (BL-236) | The suppression's own revisit trigger fired (pyjwt 2.13.0; unsuppressed audit clean, verified twice) |
| `ae16917` chore(ci): gate lockfile freshness with uv lock --check (BL-237) | Lands the 2026-05-27 engagement recommendation; verified green on the tree and failing on a deliberately drifted pyproject |
| `c3069e2` docs(audit): correct F-4 impact | Honesty fix: logfire remains transitive via `pydantic-ai-slim[...,logfire,...]`; `pip show Required-by` misses extras-conditional edges |
| `1a9e2ff` chore(deps): drop unused direct dependency logfire (BL-238) | Declaration hygiene; resolved graph unchanged (173 packages); full gate baseline-identical |
| `e3daa65` docs(audit): append D-8 | DCO sign-off documented but unpracticed and unenforced; deferred to maintainer (BL-241) |
| `3cd488d` docs(memory): tiering stamp-map caveat; _expiry wording (BL-239) | Two docstring-accuracy fixes, no behaviour change; memory tests 536 passed |
| `599b61b` docs(adr): ADR 0025 | The fourteenth-audit decision record plus index row |
| `f919099` docs(backlog): BL-236..BL-241 + root register | Backlog rows in the canonical tracker; root `BACKLOG.md` indexes into it |
| `c4cbac1` docs: post-audit sweep | D-7 drift closed (README, docs/README, runbook) and ADR 0025 recorded across CHANGELOG, STATUS, LIMITATIONS, SECURITY, CLAUDE.md |
| (this commit) docs(audit): final report | Phase 8 deliverable |

## Residual risk statement

- No known vulnerability in the locked dependency set; the audit now
  runs with zero suppressions, so the next advisory fails CI loudly.
- No secret material in the tree or reachable history; the four
  gitleaks hits are synthetic redaction fixtures (F-3).
- The pre-existing, tracked limitations are unchanged and remain the
  real risk surface: no live-model reference workload exercises the
  wired runtime end to end (`BL-120`, `LIMITATIONS.md` L2), wall-clock
  preemption stops at await boundaries (`BL-155`, L11),
  approval-resume replays the run (`BL-114`, L10), and the runtime
  adapter targets a pre-1.0 upstream (`LIMITATIONS.md` L8).
- Process risks accepted knowingly: the Python 3.13 CI matrix leg was
  not reproduced locally (`[UNVERIFIED]` here; CI covers it), and the
  flake assessment rests on a single deterministic run.
- `uv lock --check` could in principle fail on a uv-version skew
  between CI and the lockfile rather than genuine drift; the
  mitigation (pin uv in CI) is pre-agreed in ADR 0025's revisit
  triggers.

## Top 5 backlog items

1. `BL-120` (L): live-model reference workload, key-gated CI smoke;
   the highest-leverage open item, retires `LIMITATIONS.md` L2.
2. `BL-155` (L): true wall-clock preemption for non-cooperative
   tools; the watchdog currently stops at await boundaries.
3. `BL-114` (L): non-replay approval resume once upstream exposes a
   stable pause/resume primitive.
4. `BL-241` (S): DCO sign-off, enforce or reword; a governance doc
   currently declares an unverifiable requirement.
5. `BL-240` (S): secret-scan CI job with a fixtures allowlist,
   weighed against the existing compensating controls.

## Protocol deviations (declared)

- Branch: the engagement protocol names `audit/2026-06-12-full-pass`;
  this environment pins pushes to the session branch
  `claude/sleepy-fermi-l2k07d`, which was used instead. No push to
  `main` occurred.
- ADR format and status: new ADRs follow the repository's established
  ADR template and land as Accepted with the changes they record (the
  convention all 24 prior ADRs used), rather than MADR with status
  `proposed`. The proposals needing a decision are explicit backlog
  items (`BL-240`, `BL-241`) instead of proposed-status ADRs.
- `BACKLOG.md` at root is an audit-scoped register pointing into
  `docs/backlog.md`, which remains the canonical tracker, to avoid a
  second source of truth.
- Commits are not DCO-signed, matching the observed practice on
  `main` (see finding D-8 / `BL-241`).
