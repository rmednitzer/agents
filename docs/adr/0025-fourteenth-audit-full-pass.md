# ADR 0025: Fourteenth audit, full-pass protocol, process hardening

- Status: Accepted
- Date: 2026-06-12
- Authors: rmednitzer (drafted by the 2026-06-12 Claude Code assurance session)
- Builds on: ADR 0001-0024

## Context

A fourteenth audit, run under an external full-pass engagement
protocol (phased: inventory, validation baseline, security audit,
quality audit, gated remediation, documentation, ADR, backlog,
report) rather than the runbook section 2 code-walk alone. The
phase evidence lives in the `audit/` tree
(`audit/00-inventory.md`, `audit/01-baseline.md`,
`audit/02-security-findings.md`, `audit/03-final-report.md`), each
claim backed by a command run in the session; this ADR records the
decisions, the same division of labour as every prior audit ADR.

Scope: the whole tree, with the line-by-line focus on the only code
no prior audit had covered, the ADR 0024 wave (`memory/compaction.py`,
`memory/tiering.py`; `git diff --stat d704f23..HEAD` shows nothing
else changed since the thirteenth audit except docs and the
lockfile). The baseline was fully green on every declared gate
(1170 tests, 95.25 % coverage, ruff, mypy strict, schema, REUSE,
eval gate at P@1 = MRR = 1.0, pip-audit).

The recurring fault classes were re-walked against the new modules
and held by construction: the per-call `ttl_seconds` boundary the
compaction and tiering paths feed routes through
`Namespace.resolve_ttl` (BL-197) in all five adapters and all four
`transact` implementations (read individually), so the
non-finite-numeric class (BL-159 / 205 / 221 / 226 / 231 / 232) is
closed there; the compactor's best-effort source deletes and the
tiering demotion's cold-undo both already carry the BL-233 per-item
containment convention; the atomic compactor's absent-target path is
correctly CAS-create-gated (`TxnWrite(expected_version=None)` means
"must be absent"). No runtime defect was found in any swept
dimension (the no-finding tables in `audit/02-security-findings.md`
are the full enumeration).

What the pass did find sits one level up, in the gates and
declarations around the code:

- **BL-236**: the `--ignore-vuln PYSEC-2025-183` suppression in the
  `dependency-audit` job had outlived its target. The locked pyjwt is
  2.13.0 (2026-05-21) and the unsuppressed `pip-audit` run is clean,
  so the suppression's own documented revisit trigger had fired and
  nobody noticed: an ignore flag has no expiry alarm. Kept, it could
  only mask a future advisory republished under the same ID.
- **BL-237**: the `dependency-audit` job exported the lockfile with
  `uv export --frozen`, which does not assert `uv.lock` is in sync
  with `pyproject.toml`. The drift class this admits actually
  occurred (2026-05-25 specifier wave, remediated 2026-05-27, and the
  engagement record recommended exactly this gate,
  `audit/2026-05-27-engagement.md` section 6); the recommendation was
  never landed.
- **BL-238**: `logfire` was declared in `[project] dependencies` with
  zero references in source, tests, or docs. During remediation the
  impact was corrected: `pip show` had reported `Required-by: agents`
  only, but pip does not attribute extras-conditional edges, and the
  `uv.lock` `pydantic-ai` block shows
  `pydantic-ai-slim[...,logfire,...]` as a base edge, so logfire
  remains a transitive either way. The finding's substance is
  declaration hygiene: an unused direct dependency misdescribes the
  project's import surface and keeps a floor pin alive that would
  force logfire in even if upstream dropped it.
- **BL-239**: two comment-accuracy drifts. `memory/_expiry.py` still
  described the TTL API-boundary validation as a pending longer-term
  fix although BL-197 landed it; `TieredMemoryStore` did not state
  that its write-order stamp map is pruned only during
  `demote_to_capacity`, so a wrapper used without periodic capacity
  passes accumulates one entry per distinct key written (the BL-191
  growth-honesty standard applied to the new module).

## Decision

### 1. Remove the stale pip-audit suppression (BL-236)

`ci.yml`'s audit step drops `--ignore-vuln PYSEC-2025-183` and the
justification comment, replaced by a dated removal note; the two
quoted commands in `docs/runbook.md` (sections 2.2 and 5.1) are
synced. Verified: the exact new command (`uv export --frozen ...`
then `uvx --python 3.12 pip-audit --strict --progress-spinner=off -r
...`) exits 0 with "No known vulnerabilities found". The standing
rule this establishes: a suppression is re-justified or removed at
every audit; an ignore that no longer fires is removed immediately.

### 2. Gate lockfile freshness in CI (BL-237)

The `dependency-audit` job gains `uv lock --check` as its first step
after Python setup, failing the PR when `uv.lock` is stale relative
to `pyproject.toml`. Verified both ways this session: exit 0 on the
current tree; exit 1 ("The lockfile at `uv.lock` needs to be
updated") on a deliberately drifted `pyproject.toml`, restored
green afterwards. This closes the 2026-05-27 engagement
recommendation structurally: a specifier bump merged without
re-resolution now fails at the PR boundary instead of letting every
later gate validate an older dependency surface than declared.

### 3. Drop the unused `logfire` declaration (BL-238)

`[project] dependencies` loses `logfire>=4.34.0`; `uv.lock` is
re-resolved (same 173 packages; the diff is the project's own
requires-dist entry). The full gate was re-run on the changed tree
and is baseline-identical (1170 passed, 95.25 % coverage). The
lesson memorialised for future dependency audits: verify "unused"
against the lock graph, not `pip show Required-by`, which misses
extras-conditional edges.

### 4. Comment-accuracy fixes (BL-239)

Docstring-only: the `TieredMemoryStore` stamp-map growth caveat and
the `_expiry.is_expired` post-BL-197 wording. No behaviour change,
hence no new tests (the no-behaviour-change rule cuts both ways).

### 5. Engagement artifacts join the tree

The phase evidence files land under `audit/` beside the 2026-05-27
engagement record, and a root `BACKLOG.md` carries this pass's
deferred-items register. `docs/backlog.md` remains the canonical
line-item tracker; `BACKLOG.md` is an audit-scoped index into it,
stated in its header, so there is one source of truth.

### Deferred, proposed for maintainer decision

- **BL-240** (proposed): a secret-scan CI job (gitleaks) with an
  allowlist for the four synthetic `AKIA...` redaction-test fixtures
  (`tests/harness/test_redaction.py`), the only hits in tree and
  50-commit history. Today's compensating controls: GitHub secret
  scanning, CodeQL, and the audit-cadence manual scan.
- **BL-241** (proposed): CONTRIBUTING.md requires per-commit DCO
  sign-off, but no recent commit on `main` carries a
  `Signed-off-by` trailer (squash-merge drops them) and no CI check
  enforces it. Enforce or reword; a governance doc should not
  declare an unverifiable requirement. The choice is the
  maintainer's, deliberately not taken by this pass.

## Consequences

- CI is strictly tighter: one suppression removed, one new failure
  mode gated (stale lockfile). Both verified green against the
  current tree, so no landing risk.
- No runtime behaviour changed anywhere in this wave; the test count
  (1170) and coverage (95.25 %) are baseline-identical. The only
  source diffs are docstrings (BL-239).
- The dependency declaration now matches the import surface; the
  resolved graph is unchanged, so no installer-visible effect.
- The documentation tree is brought current through ADR 0024 plus
  this ADR in the same pass (the post-ADR-0024 drift in `README.md`,
  `docs/README.md`, and `docs/runbook.md` was itself a finding,
  D-7: the ADR 0024 wave updated five living docs but missed those
  three).
- Renovate keeps maintaining the lockfile; with BL-237 a Renovate PR
  that bumps a specifier without re-resolving fails its own CI run,
  which is the intended pressure.

## Revisit triggers

- If `pip-audit` starts failing on a republished `PYSEC-2025-183`,
  re-evaluate against the then-current pyjwt before considering any
  new suppression, and date-stamp it with an explicit re-check
  obligation per audit (the BL-236 rule).
- If `uv lock --check` ever fails in CI on resolver-version skew
  rather than genuine drift (uv version differences between local
  and CI), pin the uv version in the workflow alongside the action
  SHA; not done now because the gate passed against the in-tree lock
  with the setup-uv default.
- BL-240 / BL-241 dispositions (maintainer).
- The standing open items, unchanged by this audit: `BL-120` (live
  reference workload), `BL-132` / `BL-171` (prompt caching),
  `BL-113` / `BL-138` (true OTel spans), `BL-114` (deeper resume),
  `BL-155` (true wall-clock preemption), `BL-179` (`RetryPolicy`
  partial-usage accounting).
