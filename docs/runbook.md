# Runbook: audit, review, enhance, validate, extend

Operational guide for the recurring maintenance cycle on this repository: how to run the next code audit, work the open backlog, propose a refactor that survives the additive-to-L1 rule, validate the green-gate set, extend the public surface, and run a sweep across the documentation tree.

This is a runbook, not a roadmap. The roadmap lives in [`docs/backlog.md`](./backlog.md). The phase and document maturity index is [`STATUS.md`](../STATUS.md). The cross-cutting decisions are the ADRs under [`docs/adr/`](./adr/README.md), with [ADR 0007](./adr/0007-l2-implementation-wave.md) defining the additive-to-L1 rule every change in this runbook respects.

Audience: a maintainer or contributor opening a fresh PR cycle, an auditor preparing the next in-depth code audit, or a Claude agent running `/ultrareview` or the `code-review` skill against the working tree.

Last reviewed: 2026-06-12 (ADR 0026 prompt-caching capability landed after the ADR 0025 fourteenth audit; ADR 0024 was the BL-234/BL-235 capability wave; next audit slot is ADR 0027).

## 0. Conventions this runbook respects

1. Additive-to-L1 ([ADR 0007](./adr/0007-l2-implementation-wave.md)): no removal or signature change of an L1 import path. New optional keyword parameters with defaults that preserve L1 behaviour, new modules, or new Protocols beside the existing ones. Surface configuration errors at load time, not mid-run.
2. ID discipline (`docs/backlog.md`): IDs are stable; `[pending]` -> `[in-progress]` (with branch) -> `[resolved]` (with merge commit). New L3 items use the next free `BL-1xx`.
3. Documentation style (`CLAUDE.md`): no em-dashes, no `--` as prose punctuation outside HTML comments and code spans. Use commas, colons, or parentheses. Direct, technical, no marketing voice. ISO 8601 dates, 24h UTC.
4. ADR immutability (`docs/adr/README.md`): an ADR is Accepted and frozen; a later ADR supersedes an earlier one rather than editing it. Errata are recorded forward, not in place.
5. Green-gate set (`Makefile`, `.github/workflows/ci.yml`): `ruff check`, `ruff format --check`, `mypy` (strict, plugins=pydantic.mypy), `pytest` with `--cov-fail-under=94`, `gen_schema.py --check`, `reuse lint`, the `dependency-audit` job (`uv lock --check`, then pip-audit over the exported lockfile), the `secret-scan` job (gitleaks with the `.gitleaks.toml` allowlist, BL-240), and the `evaluation` job (`scripts/eval.py`, P@1=1.0 / MRR=1.0). The branch protection required context is `ci-success`, the stable aggregate.

## 1. Prep: read the truth set

Open in order, top to bottom:

1. `CLAUDE.md`. Repository purpose, layout, conventions.
2. `STATUS.md`. Phase and document maturity, today's date for "Last reviewed".
3. `LIMITATIONS.md`. The scope-boundary contract; every audit measures against this list.
4. `docs/backlog.md`. The line-item tracker. Filter for `[pending]` / `[in-progress]`.
5. `docs/adr/README.md`, then the most recent ADRs (`0023`, `0024`, `0025`) for the cross-cutting decisions in force.
6. `CHANGELOG.md` [Unreleased] section. What landed but is not yet tagged.
7. `SECURITY.md`. The hardening posture and the untrusted-content stance.

`make help` lists the developer targets. `git log --oneline -20 --decorate` shows the last ~20 merges and the audit-wave cadence (PR #20 the L2 wave, the audit ADRs 0009-0015).

## 2. Phase A: audit

The repo's audit cadence is in `docs/backlog.md`: ADR 0009 (first), ADR 0010 (second), ADR 0011 (third), ADR 0013 (fifth), ADR 0015 (sixth), ADR 0017 (seventh), ADR 0018 (eighth), ADR 0019 (ninth), ADR 0020 (tenth), ADR 0021 (eleventh), ADR 0022 (twelfth), ADR 0023 (thirteenth), ADR 0025 (fourteenth; the full-pass engagement, evidence under `audit/`). A "fourth" audit pass was folded into the cross-repo review in ADR 0012 (run-provenance + provider-batch capabilities). ADR 0014 is the BL-180 capability ADR (durable Versioned + new Transactional Protocol). ADR 0016 is the BL-133 capability ADR (skill execution isolation). ADR 0024 is the BL-234/BL-235 capability ADR (compaction, summarisation, tiering). ADR 0026 is the BL-132/BL-171 capability ADR (prompt caching on the runtime adapter). The next audit slot is ADR 0027.

### 2.1 Plan the audit

1. Branch: `claude/code-audit-<adjective>-<noun>-<slug>` (the prior audits used `claude/code-audit-improvements-3xpej`). Branch is dev-only; the merge commit is the resolution reference cited in the next ADR.
2. Scope by area, the same partition the prior audits used: `harness/`, `memory/`, `skills/`, `workloads/`, `evaluation/`, `agents/` (CLI), `scripts/`, and the docs tree. Run each area as one pass; do not interleave.
3. Audit *classes* the prior audits fixed pointwise, not only the obvious surface. The fifth audit (ADR 0013) re-applied the read-vs-CAS boundary class (BL-157 / BL-168 / BL-177) to the *listing* paths and found `BL-188`. Look for the same class extension on every fix.
4. Audit the paths the most recent dependency bumps exercise. `anthropic`, `openai`, `redis`, `pydantic-ai`, `cryptography` are the ones with major-version churn; the `OpenAIBatchProcessor.results` diagnostic gap (`BL-189`) was found this way.

### 2.2 Run the static surface

```bash
make check                        # ruff check (E,W,F,I,B,C4,UP,RUF,SIM,PT) + mypy strict + pytest
uv run ruff format --check .      # CI runs this inside the lint job; make check does not
uvx reuse lint                    # REUSE 3.x compliance (BL-152); CI runs this inside the lint job
make schema                       # regenerate JSON Schema, then diff against committed (the test suite also gates this via tests/workloads/test_schema.py)
uv run python scripts/eval.py     # the BL-130 evaluation gate (P@1 / MRR == 1.0)
uv lock --check                   # lockfile freshness against pyproject.toml (BL-237); CI runs this inside the dependency-audit job
uv export --frozen --all-extras --no-emit-project --format requirements-txt -o /tmp/audit.txt
uvx --python 3.12 pip-audit --strict --progress-spinner=off -r /tmp/audit.txt
gitleaks detect --source . --no-banner --redact --config .gitleaks.toml   # the secret-scan job (BL-240)
uv run pytest --cov=agents --cov=harness --cov=memory --cov=skills --cov=workloads --cov=evaluation --cov-fail-under=94   # CI's test job enforces 94%; make check does not
```

A green local run is the precondition. Anything that does not pass locally cannot be an audit finding (it is a CI bug; file before the audit).

### 2.3 Look for the recurring fault classes

Maintain a running checklist; an audit is just running it carefully and finding one new entry. The classes that have produced findings so far:

| Class | Trigger | Past examples |
|---|---|---|
| Read-vs-CAS / read-vs-listing expiry boundary | A live key disappears or a CAS gate flips at the exact expiry instant | `BL-157` (DynamoDB CAS), `BL-168` (SQLite sweep), `BL-177` (DynamoDB read), `BL-188` (in-tree listing) |
| Path-traversal / symlink dereference on install | A pre-existing link or a crafted local mirror at `dest/<name>` | `BL-169` (`LocalSkillSource`), `BL-172` (network sources), `BL-190` (`LocalSkillSource` clean-error) |
| O(n^2) / unbounded growth on adversarial input | Untrusted model or MCP output, deeply nested brackets, bracket-heavy bodies | `BL-159` (per-`[` restart), `BL-173` (substring slices), `BL-182` (count-vs-work), `BL-191` (span list ceiling) |
| Silent-no-op governance composition | Predicate-name collision in `compose_contracts` keeps the weaker severity | `BL-166` (pre/inv/post), `BL-174` (governance) |
| Reinterpretation of a cancellation as a pause | `BaseException` catch that swallows `CancelledError` / `BudgetExceeded` | `BL-165` |
| Bind-by-tool-only on resume | Approval for one set of arguments satisfies a different call | `BL-193` |
| Authorization carry across pause | Stale `interruption_id`, stale budget, stale guard state | `BL-154` (budgets across pause), `BL-193` (arguments across pause) |
| Audit-content over-permissiveness | Reserved keys not rejected, only-dict walk in redaction | `BL-159` (Redactor walk), `BL-167` (reserved keys) |
| Empty-batch lock | A no-op batch still takes the write lock | `BL-178` |
| First-occurrence-vs-strictest | Composition keeps the first, not the strictest | `BL-166`, `BL-174` |
| Fan-out per-member failure containment | A single failing fan-out member cancels siblings, breaks downstream telemetry / audit-vs-raise parity | `BL-222` (`MultiDispatcher` ensemble), `BL-223` (`MultiSink` audit fan-out), `BL-227` (`BoundedS3Store.evict_to_capacity` sequential DELETE), `BL-228` (`RoutingChainDispatcher` cheap-first chain), `BL-233` (`S3Store._sweep_sync` / `DynamoDBStore._sweep_sync` periodic-sweep per-item DELETE) |
| Non-finite numeric at a trust / config boundary | A `NaN` / `+inf` clamps to the top of a ranking, slips a `<= 0` guard (both comparisons are False), or disables a `consumed > limit` ceiling | `BL-159` (cosine NaN-clamp), `BL-205` (`MultiDispatcher` weights), `BL-221` (`BudgetTracker` consume side), `BL-226` (S3 metadata parse), `BL-231` (`ActionBudget` / `RetryPolicy` limit side), `BL-232` (`MCPServerSpec` / `TTLSweeper` positivity guards) |
| Open backlog item: classes still unresolved | The pending Tier 1/2 items each represent a class boundary | `BL-114` (replay vs deduplicated resume), `BL-155` (preemption vs cooperation); `BL-132/171` (cache hit/miss) graduated to ADR 0026 with the live-hit residual on `BL-120` |

### 2.4 Fix discipline

1. Each fix is additive (a strict narrowing where the prior behaviour was incorrectly accepting; `BL-193` is the template).
2. Each fix gets a regression test, named after the boundary it walks (`test_gate_resume_does_not_reuse_stale_approval_for_new_arguments`). Place it under the area's `tests/` directory; do not invent a new layout.
3. Each fix gets a `BL-1xx` ID, the next free in the section. Resolution reference is the audit's branch + the future ADR.
4. The clear correctness/security bugs are fixed in the same increment (the ADR 0009 / ADR 0011 / ADR 0013 pattern). Findings that need an unstable upstream (PydanticAI deferred resume, OTel logs SDK, prompt cache) stay `[pending]` with the dependency named.
5. If a fix is documentation-only, mark it as such in the ADR (`BL-160` is the template for an errata cluster). ADRs already merged are not edited; corrections are recorded forward.

### 2.5 Author the audit ADR

Use `docs/adr/0023-thirteenth-code-audit.md` (the latest audit ADR) as the template:

1. Status: Accepted (set on merge).
2. Context: what triggered this pass (dependency bump, sibling-repo review, the class-extension principle).
3. Decision: per finding, the diagnosis, the fix, and the test.
4. Consequences: every additive-to-L1 claim is restated, every test added is named.
5. Revisit triggers: the open items that the audit deliberately did not touch (upstream-dependent).

### 2.6 Update the ledger

For each finding, in this order:

1. `docs/backlog.md`: add the `BL-1xx` item under "Code audit (ADR 00NN, YYYY-MM-DD)" with `[resolved]`, ADR + branch as the resolution reference.
2. `LIMITATIONS.md`: update or remove the L-entry the fix closes; add a new L-entry only for a contract-level remainder.
3. `STATUS.md`: extend the "Phase tracking" table; bump the "Last reviewed" date.
4. `CHANGELOG.md`: a new `[Unreleased]` section, "Fixed" / "Security" / "Changed" / "Documentation" subsections (Keep a Changelog convention).
5. `README.md`: only if the audit changed a top-level capability list; the file is stable, not a changelog.
6. `CLAUDE.md`: extend the ADR enumeration in "Layout" and "Status and limitations". Nothing else.

## 3. Phase B: review

For an inbound PR, run `code-review` on the diff or `verify` to drive the change. Frame the review against the same fault classes as the audit. Specifics that match this repo:

1. Confirm additive-to-L1. An optional keyword with a default that preserves prior behaviour, or a new module, or a side-by-side Protocol. Removal of an L1 path is a reject regardless of test status.
2. Confirm the test covers the boundary, not only the happy path. The audit-fix names in `tests/` are the calibration.
3. Confirm REUSE compliance: `uvx reuse lint`. A new file is covered by the tree-wide `REUSE.toml`; only investigate a `BUG` line.
4. Confirm the schema is regenerated: `make schema` then `git diff docs/schema/`. A manifest-model change without schema regen breaks `gen_schema.py --check`.
5. Confirm DCO sign-off (`Signed-off-by:` trailer, `CONTRIBUTING.md`).
6. Confirm Conventional Commit prefix (`feat:` / `fix:` / `docs:` / etc.).
7. Confirm the backlog ID is referenced from the commit message, the ADR (if new), and the regression test name.
8. Confirm the `[Unreleased]` CHANGELOG entry is in the matching subsection.
9. If the diff is to `harness/` or `memory/` contracts, or to skill/workload loading, or to event content, confirm the PR description states the threat considered and the residual risk (`CONTRIBUTING.md` "Security review").

`/ultrareview` is a multi-agent cloud review; reserve it for a release candidate or a contract-level change. A normal PR uses the `code-review` skill at the appropriate effort level.

## 4. Phase C: enhance (backlog work)

### 4.1 Pick an item

Open `docs/backlog.md`, filter to `[pending]` / `[in-progress]`. The current open set, by tier:

| ID | Tier | Size | One-line shape | Dependency |
|---|---|---|---|---|
| `BL-120` | Tier 1 | L | A real reference workload exercising the wired runtime against a live model (now also the live cache-hit gate for the ADR 0026 wiring) | A funded provider key, a credentialed CI gate skipped without it |
| `BL-113` | Tier 2 | L | True OTel spans + trace-context propagation | The OTel logs SDK stabilising (the GA cut; still `opentelemetry.sdk._logs` at 1.39.1, checked 2026-06-12) |
| `BL-138` | Tier 2 | M | OTel GenAI semantic conventions on `BL-113`'s spans | Same upstream as `BL-113`; depends on it |
| `BL-114` | Tier 2 | L | Deeper PydanticAI resume via `DeferredToolRequests` / `message_history` | PydanticAI's pause/resume primitive stabilising |
| `BL-155` | Tier 2 | L | True wall-clock preemption for non-cooperative tools | A thread/process execution boundary (not the asyncio await pattern) |
| `BL-179` | Tier 2 | M | `RetryPolicy` token / step accounting from intermediate attempts | Upstream PydanticAI partial-usage on the exception path |

### 4.2 Item-level workflow

For an item with no upstream dependency (the "ready" set today: `BL-120`):

1. Move `[pending]` to `[in-progress]` with the branch name. Push the change as a separate commit so an open backlog state is visible.
2. Design the surface. Write the new Protocol or the new optional keyword before any implementation. Surface it in the module docstring; an L3 keyword is read once, supported forever.
3. Build behind a flag. The default reproduces prior control flow and exceptions byte-for-byte. Run the existing suite green before adding the new tests.
4. Add tests in the order: unit (`tests/<area>/`), integration (`tests/<area>/test_*_integration.py` if present), then a regression test for the boundary.
5. Update the green gate when adding a new top-level component: the mypy package list in `Makefile` `type-check` (today `agents harness memory workloads skills evaluation`) and the `--cov=...` list in `.github/workflows/ci.yml`'s `test` job. Pytest discovery is already set via `testpaths = ["tests"]` in `pyproject.toml` and does not need a path edit.
6. Update the docs in the same PR: the area `README.md`, the ADR (if cross-cutting), the `[Unreleased]` CHANGELOG, the backlog `[resolved]` line.
7. Open the PR. CI runs the seven jobs (`lint`, `type-check`, `test`, `dependency-audit`, `secret-scan`, `evaluation`, `ci-success`); `ci-success` is the one required context.

### 4.3 Item-level workflow with an upstream dependency

For an item gated on an unstable upstream (the "tracked, not rushed" set: `BL-113` / `BL-138`, `BL-114`, `BL-155`, `BL-179`; `BL-132` / `BL-171` graduated this way when pydantic-ai 1.106 shipped the verified cache API, ADR 0026):

1. Do not ship a no-op flag. The "no half-finished implementation" bar (`CLAUDE.md` "Doing tasks") forbids it.
2. Do ship the documentation: revisit triggers in `LIMITATIONS.md`, the upstream signal to watch (e.g. PydanticAI release notes, OTel logs SDK GA cut).
3. If the upstream surface has shipped behind a feature flag, write a small spike behind an explicit `experimental_*` kwarg; this is the only case where a partial implementation is acceptable (the kwarg is the load-bearing contract; default off).

### 4.4 The reference workload (`BL-120`)

The longest-tracked open item. The work splits into four parts:

1. The workload bundle: `workloads/<name>/manifest.yaml` (one real provider model), `__main__.py` (the entry point), `contract.py` (the `WorkloadContract` per `workloads/_example`'s shape).
2. The credential gate: a `pytest.mark.skipif` keyed on the provider's env variable (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`), the same shape as the existing `pytest.importorskip` guards.
3. The CI wiring: a new job in `.github/workflows/ci.yml` (`live-model-smoke`), required only if a repository secret is present; not in the `ci-success` aggregate (a missing key must not fail a fork's PR).
4. The provenance check: enable `record_sink` (`BL-185`), persist a `RunRecord`, run `scripts/check_run_records.py` against the persisted corpus.

This work unblocks the credentialed half of `LIMITATIONS.md` L6 (the eval-gate-vs-live-model boundary) and is the live-validation gate for the `BL-132` / `BL-171` cache wiring (ADR 0026: a second identical-prefix run asserting `cache_read_tokens > 0` closes the L9 residual).

## 5. Phase D: validate

### 5.1 The green-gate set

The CI jobs in `.github/workflows/ci.yml` and what each one runs:

1. `lint`: `uv run ruff check .`, `uv run ruff format --check .`, and `uvx reuse lint` (REUSE 3.x compliance, `BL-152`). One job, three checks.
2. `type-check`: `uv run mypy agents harness memory workloads skills evaluation`.
3. `test` (matrix `python: ["3.12", "3.13"]`): `uv run pytest --cov ... --cov-fail-under=94`. The suite includes `tests/workloads/test_schema.py::test_gen_schema_check_passes` which gates `scripts/gen_schema.py --check` (so a stale `docs/schema/*.json` fails the test job, not a separate CI job).
4. `dependency-audit`: `uvx --python 3.12 pip-audit --strict ... -r audit-requirements.txt` over the exported lockfile (`BL-150`, `BL-194`).
5. `evaluation`: `uv run python scripts/eval.py --min-p-at-1 1.0 --min-mrr 1.0` (`BL-130`).
6. `ci-success`: the aggregate that requires jobs 1-5 to have succeeded. Branch protection's required context on `main`.

A separate `analyze (python)` job from `.github/workflows/codeql.yml` runs CodeQL on push, pull request, and weekly; it is not part of `ci-success`.

What each job depends on locally:

```bash
make check                                                                  # covers ruff check + type-check + the pytest leg (no coverage threshold, no ruff format --check, no reuse lint)
uv run ruff format --check . && uvx reuse lint                              # the rest of the lint job
uv run pytest --cov=agents --cov=harness --cov=memory --cov=skills --cov=workloads --cov=evaluation --cov-fail-under=94  # the test job's coverage leg
uv run python scripts/eval.py --min-p-at-1 1.0 --min-mrr 1.0                # the evaluation job
uv lock --check && \
  uv export --frozen --all-extras --no-emit-project --format requirements-txt -o /tmp/audit.txt && \
  uvx --python 3.12 pip-audit --strict --progress-spinner=off -r /tmp/audit.txt   # the dependency-audit job
gitleaks detect --source . --no-banner --redact --config .gitleaks.toml          # the secret-scan job (BL-240)
```

For the schema, regenerate and confirm no drift:

```bash
make schema && git diff --exit-code docs/schema/
```

If `git diff` is non-empty, a manifest model changed; commit the regen with the change that caused it.

### 5.2 What the gates do not catch

Per `LIMITATIONS.md`:

- L2: no live-model behaviour is exercised (until `BL-120`).
- L6: only deterministic dispatch P@1 / MRR is gated; an LLM-dispatcher / live-runtime trajectory suite is not gated.
- L7: spans are log records carrying trace IDs as attributes, not a true span tree; flame graphs are not available out of the box.
- L11: wall-clock preemption is at await boundaries, not against fully blocking tools.

For a pre-release rehearsal, supplement the green gates with the `verify` skill against the `_example` workload, and (when `BL-120` lands) the live-model smoke job.

### 5.3 Branch-protection check

The required contexts on `main` are `lint`, `type-check`, `ci-success` (the `BL-162` repointing). If a PR shows "Expected, waiting for status to be reported", the protection was repointed at a stale name; the resolution is to fix the protection, not to relax the gate.

## 6. Phase E: extend

Per `CLAUDE.md` "Adding components". The L1 surface is frozen; an extension is an addition.

### 6.1 New workload

```
workloads/<name>/
  README.md          one-paragraph contract
  __init__.py
  __main__.py        the entry point
  contract.py        the WorkloadContract
  manifest.yaml      skills, runtime, memory namespace, exit conditions
```

Tests: `tests/workloads/<name>/`. If the manifest references a new skill, add the skill first; the `BL-011` validator catches an unresolved `skills:` entry.

### 6.2 New skill

```
skills/<name>/
  SKILL.md           YAML frontmatter (name, description) per the Agent Skills spec
  references/        optional static knowledge
  scripts/           optional bundled scripts
  contract.py        optional, skill-level contract (composed with the workload contract)
```

Tests: `tests/skills/<name>/`. The skill name must match the directory; the `BL-010` shape applies.

### 6.3 New harness module

```
harness/<concern>.py        contract documented in a module docstring
tests/harness/test_<concern>.py
```

If the module changes an existing contract, write an ADR; if additive, no ADR is needed, but the area `README.md` is updated.

### 6.4 New memory backend

The five existing adapters (`inmemory`, `sqlite`, `redis`, `s3`, `dynamodb`) are the template. The new adapter implements the `MemoryStore` Protocol in `memory/<backend>.py`, and only the extension Protocols (`BatchMemoryStore`, `ScanMemoryStore`, `ContentAddressableMemoryStore`, `CASMemoryStore`, `SweepableMemoryStore`, `SemanticMemoryStore`, `VersionedMemoryStore`) the backend can honour, not faked. The `wrap_*` factories (`BL-156`) compose decorators that forward exactly the supported Protocols; reuse the factory pattern.

Third-party drivers are lazily imported in `__init__` with a clear error naming the extra; the extra is declared in `[project.optional-dependencies]`. The package imports and type-checks without the driver.

### 6.5 New top-level component (rare)

`evaluation/` is the most recent example (`BL-130`, ADR 0011). It added a new module tree, `tests/evaluation/`, an entry in the mypy / pytest paths (`Makefile`), a blocking CI job (`evaluation` in `.github/workflows/ci.yml`), and a CLI driver (`scripts/eval.py`). Repeat that shape for any new top-level component.

## 7. Phase F: cleanups, consolidations, refactoring

Cleanups stay additive. Three classes of cleanup work are safe; the rest are deferred until a contract revision is on the table.

### 7.1 Safe cleanups (no ADR needed)

- Test-only refactors: extracting a fixture, deduplicating a setup, renaming a test for its boundary. The tests stay green and the public surface is unchanged.
- Docstring cleanups: stale BL references, outdated line citations, wording. The `BL-160` errata cluster is the template; if a cluster of docstrings drifts together, treat them as a single docs-only PR.
- Internal helper consolidation: a private `_*` helper used in one module, moved to a sibling module without changing its signature.

### 7.2 Consolidations (ADR required only if cross-cutting)

- One audited path for a class of behaviour: the `_prepare_install_dir` consolidation (`BL-172` -> `BL-190`) factored the symlink-safe-clear into one place across `LocalSkillSource`, `GitHubSkillSource`, and `MarketplaceSkillSource`. The class extension test is "would this fix apply to another callsite in the same shape?".
- `wrap_*` factory introduction (`BL-156`): the bare `EncryptedStore(...)` / `ACLStore(...)` constructors are kept (L1/L2 compatibility), and the factory composes a decorator subclass that forwards exactly the extension Protocols the wrapped store satisfies. Use this template for the next time a wrapper layer needs to forward Protocols selectively.
- Audit-comment correction in place: if a fix-comment makes a wrong assertion (the `BL-168` comment that wrongly claimed listing-vs-CAS agreement, corrected in `BL-188`), update the comment with the next audit's fix. ADRs are not edited; comments are.

### 7.3 Refactoring (ADR required)

- Anything that changes an L1 import path or signature. This includes: renaming a public symbol, narrowing a parameter type, removing a kwarg. None of these are currently planned (the L1 surface is stable; the additive rule has not failed to accommodate a change yet).
- Anything that changes the harness or memory contracts. Per `CLAUDE.md` "Risk", state blast radius (which components, which contracts, rollback path) in the PR description.
- Anything that removes a top-level component. None planned.

### 7.4 Consolidation candidates open today

These are *candidates*, not commitments. A consolidation is worth doing only when the next audit finds a third instance of the pattern.

1. The expiry-boundary class. `BL-157`, `BL-168`, `BL-177`, `BL-188`, plus the BL-180 DynamoDB conditions. **Resolved (`BL-195`)**: `memory._expiry.is_live` / `is_expired` is the one Python-side predicate; every adapter's read / CAS / scan / sweep call routes through it. The SQL counterpart (`expires_at < :now`) and the DynamoDB DSL counterpart (`exp >= :now`) are documented in the helper's module docstring as the same invariant in a different encoding; the SQL/DSL stay literal because they execute server-side, but the docstring binds them to `is_live` so the next audit can re-derive both forms from one source.
2. The `_balanced_spans` / `first_json_array` parse-work / span-list bounds. `BL-159`, `BL-173`, `BL-182`, `BL-191`. Four classes (per-`[` restart, substring slices, count-vs-work, span-list ceiling) on one extractor. A "bounded JSON-array extractor" Protocol would make the contract explicit (a bounded parse budget, a bounded result count); the present implementation is already there in spirit, but the contract is implicit. Treat as candidate; revisit if a fifth bound is added.
3. The `RetryPolicy` accounting gap (`BL-179`). The retry accounting is per-final-attempt because PydanticAI raises without partial usage. If a partial-usage shape is back-portable in-tree (a usage-stamp at each attempt the harness collects itself), the accounting could be exact without waiting on upstream. Cost: a per-attempt collector; benefit: a contract-level fix instead of a documented gap.
4. The `EncryptedStore` legacy migration. `BL-181` ships an authenticated legacy fallback under the current key only. **Resolved (`BL-196`)**: an opt-in `legacy_multi_key=True` on `EncryptedStore` / `wrap_encrypted` over an `IterableKeyProvider` (the in-tree `RotatingKeyProvider` matches; the Protocol is the extension point for a KMS-backed provider) iterates the historical key ring on the legacy fallback path. AES-GCM authentication still gates each attempt (false-tag probability ``2**-128`` per key, accumulated ``N * 2**-128`` across the ring), answering the "is the auth tag strong enough" open question affirmatively. The default is unchanged so a KMS-backed provider that charges per call keeps its current-key-only behaviour, matching the additive-to-L1 stance.

5. The best-effort-DELETE maintenance observability gap. `BL-227` (`BoundedS3Store.evict_to_capacity`) and `BL-233` (`S3Store._sweep_sync` / `DynamoDBStore._sweep_sync`) both contain a per-item DELETE `Exception` and return the count of successes, so a *persistent* per-item DELETE failure (an IAM credential that can List / Head / Scan but not Delete) is best-effort-silent: the maintenance op returns a low count every cycle with no `TTLSweeper.failures_total` signal, because the inspection step that would surface a broader credential failure still succeeds. Two instances of the same gap. A third, or an operator report of a silent no-op sweep, would justify a sweep / evict return shape carrying both a success count and a failure count, surfaced through the sweeper's failure counters. Treat as candidate; the per-op `int` return is the current contract, so a richer shape is a `TTLSweeper`-contract change (ADR required). Tracked as the ADR 0023 revisit trigger.

A consolidation PR cites the candidate by its bullet number above; the ADR (if cross-cutting) names the cited point.

## 8. Phase G: detailed update run for every `.md` file

Run this sweep once per audit (before authoring the ADR), and once per dependency-bump quarter (Q1 / Q2 / Q3 / Q4) regardless of audit cadence. The goal is a snapshot of the documentation tree that agrees on the same `[Unreleased]` content, the same "Last reviewed" date, and the same backlog state.

The list below covers every `.md` file in the repository (excluding `LICENSES/` and `.git/`). For each, the table records: the path, the maturity (per `STATUS.md`), the specific items the sweep checks, and the trigger to update it.

### 8.1 Top-level documents (seven files)

| Path | Maturity | What this sweep checks | Update trigger |
|---|---|---|---|
| `README.md` | stable | Status paragraph cites the latest ADR (today `0026` + `BL-132` / `BL-171`, plus the ADR 0025 `BL-236`-`BL-239` audit, the ADR 0024 `BL-234` / `BL-235` wave, the ADR 0023 `BL-233` addition, and the earlier audit-wave enumeration back through ADR 0017); the capability bullets match the present `harness/` / `memory/` / `skills/` / `evaluation/` exports (including the `model_settings` pass-through + cache surfacing, `TransactionalMemoryStore`, `MemoryCompactor` / `TruncatingSummarizer`, `TieredMemoryStore`, `BoundedSweepableStore` and `BoundedRedisStore` / `BoundedDynamoDBStore` / `BoundedS3Store`); the install line lists every optional extra (`redis`, `aws`, `crypto`, `otel`, `anthropic`, `openai`); the seven-dispatcher count (`BL-160` errata) | A new ADR, a new top-level capability, a new extra |
| `CLAUDE.md` | stable | The ADR enumeration (today `0007`-`0026`); the layout block matches `ls`; the `evaluation/` line ships; the additive-to-L1 rule wording is the current canonical phrasing | A new ADR, a new top-level component, a layout change |
| `STATUS.md` | living | Last-reviewed date is today; the phase-tracking table cites the latest ADR; the document-maturity table covers every `.md` in the tree (the table mentions `0001-0026`); the L3-open row is the current `[pending]` set | Every audit, every release rehearsal |
| `LIMITATIONS.md` | living | Last-reviewed date is today; the L-entries map to the open `BL-1xx` set; an L-entry the audit closed is removed (and the close noted in the ADR); a new L-entry is added only for a contract-level remainder | Every audit |
| `CHANGELOG.md` | living | `[Unreleased]` covers everything not yet tagged; the per-section subsections (`Added` / `Fixed` / `Security` / `Changed` / `Documentation`) match the diff; ISO dates; no em-dashes | Every PR with a material change |
| `CONTRIBUTING.md` | stable | The DCO certification note (PR-submission based, `BL-241`); the REUSE compliance note; the green-gate set matches `.github/workflows/ci.yml` (today: lint, type-check, test, dependency-audit, secret-scan, evaluation); the governance section | A change to CI, a change to the contributing flow |
| `SECURITY.md` | stable | The hardening posture list covers the latest defence-in-depth fixes (today the ADR 0025 fourteenth-audit supply-chain additions: `BL-236` stale-suppression removal and `BL-237` lockfile-freshness gate on the dependency-audit job; plus the ADR 0023 thirteenth-audit addition: `BL-233` `S3Store._sweep_sync` / `DynamoDBStore._sweep_sync` per-item DELETE containment so one transient backend error cannot abort the whole periodic sweep pass and strand every later expired item, extending the BL-227 eviction-path containment to the sibling sweep path; plus the ADR 0022 twelfth-audit `BL-231` / `BL-232` non-finite numeric configuration validation so a `NaN` / `+inf` limit cannot silently disable a budget ceiling and a `NaN` sweep interval cannot drive a self-inflicted busy-sweep; plus the ADR 0021 eleventh-audit `BL-229` S3 metadata-scan HEAD not-found containment so a concurrently-deleted object cannot crash `sweep_expired` / `evict_to_capacity`; plus the ADR 0020 tenth-audit `BL-226` S3 user-metadata trust-boundary parsing and `BL-227` `BoundedS3Store.evict_to_capacity` per-key delete containment, the ADR 0019 `BL-223` `MultiSink` per-sink failure containment, the ADR 0018 set `BL-219` / `BL-220` / `BL-221` / `BL-222`, and the ADR 0017 set `BL-216` / `BL-217`); the untrusted-content section is the current canonical wording; the scope section covers every load surface; the supported-version line matches `STATUS.md` | A change to a load surface, an audit that adds a hardening item |

### 8.2 `docs/` (six files plus the ADR set)

| Path | Maturity | What this sweep checks | Update trigger |
|---|---|---|---|
| `docs/README.md` | stable | The ADR enumeration matches `docs/adr/README.md`; the runtime-providers / releasing / backlog / schema bullets stay current; today the runbook bullet is added | A new ADR, a new top-level `docs/*.md` |
| `docs/runbook.md` | living | (this file) Last reviewed; the open backlog set in 4.1 matches `docs/backlog.md`; the fault-class table in 2.3 covers every audit finding | Every audit, every quarterly sweep |
| `docs/backlog.md` | living | The L2 / L3 / audit sections agree with the merged state; status transitions follow the convention; new IDs use the next free `BL-1xx` | Every PR that closes / opens a backlog item |
| `docs/releasing.md` | stable | The versioning policy (semver from `0.1.0`, pre-1.0 `0.0.x`); the release process steps; the per-backend backup/restore notes; the tracking line cites `BL-151` (signed publish-to-index; `BL-150` resolved 2026-05-25) | A release-process change, a new memory backend |
| `docs/runtime-providers.md` | stable | The `provider:model` table covers the supported provider prefixes; the credential-variable list matches PydanticAI; the line on the `_model_free_dispatcher` honouring a `keyword` / `embedding` manifest dispatcher (`BL-161`); the current state on `BL-120` | A change to the runtime adapter, a PydanticAI provider matrix change |
| `docs/schema/README.md` | stable | The generated artefacts (today: `workload-manifest.json`, `skill-manifest.json`, `run-record.json`); the "do not edit by hand" line; the `gen_schema.py` regeneration command | A new Pydantic model exposed to schema |
| `docs/adr/README.md` | stable | The ADR table covers every `docs/adr/00NN-*.md`; the latest row is the latest ADR | A new ADR |
| `docs/adr/0001`-`0026` | stable, Accepted | Frozen; errata are recorded in the next ADR, not edited in place (`ADR 0009 -> 0010` errata template) | Never |

### 8.3 Component `README.md` (eight files)

The convention: one paragraph stating the component's contract, followed by examples and references. Every component directory under `workloads/`, `skills/`, `harness/`, `memory/` has one (`CLAUDE.md` "Quality bar").

| Path | What this sweep checks | Update trigger |
|---|---|---|
| `agents/README.md` | The CLI surface matches `agents/cli.py`; today: `workloads list`, `skills list`, `skills install <name> --from <src>`, `run <wl> <q> [--json]` | A CLI subcommand change |
| `harness/README.md` | The module enumeration matches `harness/` (today: `anthropic_api`, `budgets`, `composition`, `contract`, `drift`, `enforcement`, `errors`, `events`, `guard`, `interruption`, `mcp`, `openai_api`, `otel`, `provenance`, `recovery`, `redaction`, `runtime`, `sinks`, `tools`) | A new harness module |
| `memory/README.md` | The adapter set (in-tree / sqlite / redis / s3 / dynamodb); the extension Protocols (`BatchMemoryStore`, `ScanMemoryStore`, `ContentAddressableMemoryStore`, `CASMemoryStore`, `SweepableMemoryStore`, `SemanticMemoryStore`, `VersionedMemoryStore`); the `wrap_*` factory note (`BL-156`); the CAS-under-encryption deviation (`L12`) | A new adapter, a new extension Protocol |
| `skills/README.md` | The seven router dispatchers (`BL-160` errata); the install sources (local / github / marketplace) and the bounded-extraction posture; the `default_dispatcher` factory | A new dispatcher, a new install source |
| `workloads/README.md` | The in-tree vs out-of-tree loading (`load_workload_from_path`, `load_workload_from_entry_point`); the trust boundary (`L14`); the manifest fields | A loader change |
| `evaluation/README.md` | `evaluate_dispatch` / `evaluate_trajectory`; the golden-set location; the `scripts/eval.py` driver; the CI thresholds (P@1 = 1.0, MRR = 1.0) | A metric change, a golden-set change |
| `scripts/README.md` | The scripts (today: `gen_schema.py`, `eval.py`, `check_run_records.py`); each script's purpose | A new script |
| `tests/README.md` | The test layout mirrors the source; the doubles in use (`fakeredis`, `moto`, `TestModel`, `FunctionModel`); the conftest pattern | A new test-only dependency |

### 8.4 In-tree skill bundles (seven files)

| Path | What this sweep checks | Update trigger |
|---|---|---|
| `skills/dispatcher-skill/SKILL.md` | YAML frontmatter (`name`, `description`); the description is specific enough for the router | A description change |
| `skills/example/SKILL.md` | Same; this is the canonical compliance baseline | A spec change |
| `skills/shell/SKILL.md` | Same; the trigger description is unambiguous | A description change |
| `skills/shell/references/bash-robust-scripting.md` | Static knowledge bundled with the skill; the markdown style rule applies (no em-dashes); the content is current bash advice | A bash-advice update |
| `skills/shell/references/bash-safety-and-pitfalls.md` | Same; the security advice does not contradict `SECURITY.md` | A safety-content update |
| `skills/shell/references/quick-reference.md` | Same; the commands are current | A shell-command surface change |
| `skills/shell/references/ssh-in-depth.md` | Same; the SSH advice is current and consistent with the team's SSH posture | An SSH-posture change |
| `skills/shell/references/terminal-automation-alternatives.md` | Same; alternatives are still maintained upstream | A tool's upstream end-of-life |

### 8.5 Workload bundles (one file today)

| Path | What this sweep checks | Update trigger |
|---|---|---|
| `workloads/_example/README.md` | The one-paragraph contract; the `manifest.yaml` reference; "model: none, no model call" matches the docs' "current state" line; the markdown style rule (no em-dashes) | A `_example` change |

When `BL-120` lands, this section gains a row per new bundle.

### 8.6 Per-file sweep procedure

For each row above, in one pass:

1. Open the file. `Read` rather than `cat` (the harness logs the operation, the audit trail records it).
2. Apply the "What this sweep checks" column as a checklist. Where a check fails, note the file path and the specific gap (line citation, current state, expected state).
3. Note all gaps in a single working list (not per-file). Do not edit yet.
4. Group the gaps by trigger. A single trigger (e.g. a new ADR) usually causes a coherent block of edits across `README.md`, `CLAUDE.md`, `STATUS.md`, `LIMITATIONS.md`, `CHANGELOG.md`, and the matching component `README.md`. Edit the block in one PR.
5. After every edit, `make check && uv run python scripts/eval.py && make schema`. A docs-only PR still runs the full green gate (the doctest / link surface lives there).
6. Update `STATUS.md` "Last reviewed" date last, after the rest of the sweep is committed; the date is the contract.

### 8.7 Style and compliance pass

After the content sweep, run the style and compliance pass once across every modified file:

1. No em-dashes, no `--` as prose punctuation outside HTML comments and code spans. The repo's own markdown rule, dogfooded by `workloads/_example`.
2. ISO 8601 dates (`YYYY-MM-DD`), 24h time, UTC unless explicit.
3. No marketing voice, no emojis. Direct, technical.
4. Line citations are symbol references (the canonical `module.symbol`), not line numbers. Line numbers drift with every edit; the `docs/runtime-providers.md` BL-160 errata fixed exactly this class.
5. REUSE: a new `.md` file is covered by the tree-wide `REUSE.toml`; `uvx reuse lint` runs in CI. No per-file header is required.
6. Inline links: prefer relative paths within the repo (`./adr/0013-fifth-code-audit.md`); external links use full URLs.

A `grep -RIn -e '\-\-' -e '—' --include='*.md'` (excluding `LICENSES/`, `.git/`) is a cheap regression check against the em-dash rule. A `grep` hit inside a backticked flag (e.g. `--all-extras`, `--require-hashes`) is expected; the rule targets the punctuation dash.

## 9. Cycle calendar

| Cadence | Trigger | What runs |
|---|---|---|
| Per PR | A change to source or docs | Phase B (review). The author runs Phase D locally before opening; the reviewer runs the same on the diff. |
| Per backlog item closed | `[pending]` -> `[resolved]` | Phase C (4.2 or 4.3), the per-item workflow. |
| Per quarter | Q1 / Q2 / Q3 / Q4 | Phase A (audit) and Phase G (docs sweep). The audit's ADR is the deliverable. |
| Per release rehearsal | A `v0.0.x` tag candidate | Phase D end-to-end plus `docs/releasing.md` step 2 (bump, CHANGELOG promotion). |
| Per dependency bump cluster | A cluster of Renovate PRs landed | A targeted Phase A pass on the paths the bumps exercise (the ADR 0013 trigger). |

## 10. References

- [`CLAUDE.md`](../CLAUDE.md): conventions, additive-to-L1 rule.
- [`STATUS.md`](../STATUS.md): phase and document maturity.
- [`LIMITATIONS.md`](../LIMITATIONS.md): scope boundaries and known gaps.
- [`CHANGELOG.md`](../CHANGELOG.md): material changes by phase.
- [`SECURITY.md`](../SECURITY.md): hardening posture, untrusted-content stance.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md): the PR contract, DCO, governance.
- [`docs/backlog.md`](./backlog.md): the line-item tracker.
- [`docs/adr/README.md`](./adr/README.md): the cross-cutting decisions index.
- [`docs/releasing.md`](./releasing.md): versioning, release, operations.
- [`docs/runtime-providers.md`](./runtime-providers.md): how a workload reaches a model.
