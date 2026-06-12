# 2026-06-12 full pass: Phases 2 and 3 findings register

Security findings carry `F-` IDs, code-quality findings (Phase 3)
carry `Q-` IDs, documentation-drift findings carry `D-` IDs. Every
evidence cell cites a command run in this session. Severity ladder:
critical / high / medium / low / info.

## 1. Findings

### F-1: stale pip-audit suppression `PYSEC-2025-183` in CI

| Field | Value |
|---|---|
| Severity | low (security process hygiene) |
| CWE | none (gate configuration, not a code weakness) |
| Location | `.github/workflows/ci.yml:98` (flag + 11-line justification comment), `docs/runbook.md:53` and `:194` (quoted command) |
| Evidence | `uvx --python 3.12 pip-audit --strict --progress-spinner=off -r /tmp/audit-requirements.txt` (no ignore flag) exits 0 with "No known vulnerabilities found" against the current lockfile export. `pyjwt` is still in the tree at 2.13.0 (released 2026-05-21, after the suppression was added; `grep -n pyjwt uv.lock`), so the advisory no longer matches the locked version. The comment's own revisit triggers ("the advisory is withdrawn ... or pyjwt ships a hardened default") have fired. |
| Exploit plausibility | None directly. Residual risk is masking: a future advisory republished under the same ID would be silently ignored, and the flag misleads readers about the current risk surface. |
| Recommended fix | Remove `--ignore-vuln PYSEC-2025-183` and the justification comment from `ci.yml`; sync the two quoted commands in `docs/runbook.md`. |
| Effort / disposition | S; **fixed this pass** (tracked as `BL-236`) |

### F-2: no lockfile-freshness gate in CI

| Field | Value |
|---|---|
| Severity | low (supply-chain process) |
| CWE | none |
| Location | `.github/workflows/ci.yml:77` (`uv export --frozen ...` in the `dependency-audit` job) |
| Evidence | `--frozen` uses `uv.lock` as-is without asserting it matches `pyproject.toml` (uv semantics; `--locked`/`uv lock --check` assert freshness). The D10 drift class this enables actually occurred and was remediated on 2026-05-27 (`audit/2026-05-27-engagement.md` section 4.2), and that engagement's section 6 recommendation to add `uv lock --check` was never landed (`grep -rn "lock --check" .github/workflows/` returned nothing before this pass). `uv lock --check` passes against the current tree, so the gate can be added green. |
| Exploit plausibility | Low: a specifier bump merged without re-resolution makes CI validate an older dependency surface than declared; pip-audit then audits versions that are not the declared minimums. Process risk, not direct exploitability. |
| Recommended fix | Add `uv lock --check` as the first step of the `dependency-audit` job; document in `docs/runbook.md` section 2.2 / 5.1. |
| Effort / disposition | S; **fixed this pass** (tracked as `BL-237`) |

### F-3: gitleaks reports 4 candidate secrets (false positives)

| Field | Value |
|---|---|
| Severity | info |
| CWE | n/a (no actual credential) |
| Location | `tests/harness/test_redaction.py:42`, `:62`, `:70`, `:127` |
| Evidence | `gitleaks detect --source . --no-banner --redact` over the working tree and 50 commits of history: 4 findings, rule `aws-access-token`, all the literal string `AKIA1234567890ABCDEF`. Read in place: each is a deliberately fake, well-formed AWS key id used as a fixture to prove `harness.Redactor` redacts secret-shaped values. No other hit anywhere in tree or reachable history. |
| Exploit plausibility | None; the value is a textbook dummy and the file is a redaction test. No rotation needed. |
| Recommended fix | None required. If a secret-scan CI job is ever adopted, ship a `.gitleaks.toml` allowlist scoped to these fixture lines (proposed as `BL-240`). |
| Effort / disposition | S (only if the CI job is adopted); **deferred to backlog** |

### F-4: unused direct base dependency `logfire`

| Field | Value |
|---|---|
| Severity | low (dependency declaration hygiene; impact corrected during remediation, see Evidence) |
| CWE | none (declaration hygiene; the component itself remains a transitive either way) |
| Location | `pyproject.toml:28` (`"logfire>=4.34.0"` in `[project] dependencies`) |
| Evidence | `grep -rn logfire` over `agents harness memory skills workloads evaluation scripts tests` and all `*.md`: zero references, so the project imports nothing from it. Impact correction established during remediation: logfire is NOT removed from installs by dropping the declaration, because the base dependency `pydantic-ai` requires `pydantic-ai-slim[...,logfire,...]` (verified in the `uv.lock` `pydantic-ai` block; `uv pip show logfire` had reported `Required-by: agents` only because pip does not attribute extras-conditional edges). After removal the lock still resolves 173 packages and loses only the project's own requires-dist entry (2 lines). |
| Exploit plausibility | None today (`pip-audit` clean). The materially load-bearing issue is declaration hygiene, not install surface: an unused direct dependency misdescribes the project's import surface, keeps a Renovate-tracked floor pin alive indefinitely, and would keep forcing logfire in even if upstream `pydantic-ai` dropped it. |
| Recommended fix | Remove from `[project] dependencies`, re-resolve `uv.lock`, re-run the full gate. Re-adding later is a one-line change. Pre-1.0 with no published consumers (`STATUS.md` Release), so the install-contract blast radius is nil; the resolved graph is unchanged. |
| Effort / disposition | S; **fixed this pass** (tracked as `BL-238`) |

## 2. Phase 2 dimensions audited with no finding

Each row lists what was checked and the session evidence.

| Dimension | Result | Evidence |
|---|---|---|
| Dependency vulnerabilities | clean | CI-parity pip-audit and unsuppressed pip-audit both exit 0 (Phase 1 table) |
| Secret material (tree + history) | none | gitleaks over 50 commits: only the F-3 fixtures |
| Lockfile integrity | hash-pinned | 1809 `sha256:` entries in `uv.lock`; export uses `--require-hashes`-compatible format |
| Typosquat-adjacent dependency names | none | direct deps reviewed by hand: `pydantic-ai`, `pydantic`, `logfire`, `pyyaml`, extras `redis`/`boto3`/`opentelemetry-sdk`/`opentelemetry-exporter-otlp-proto-http`/`cryptography`/`anthropic`/`openai`; all canonical upstream names |
| GitHub Actions pinning | all pinned | 18 `uses:` references, 0 without a 40-hex SHA (`grep` count) |
| CI permissions | least privilege | workflow-level `contents: read` in all three workflows; job-level escalation only in CodeQL (`security-events: write`) and release (`contents/id-token/attestations: write`) |
| Injection (SQL) | parameterized | `memory/sqlite.py`: all values bound via `?`; the only interpolation is the table identifier, derived from the namespace name validated against `^[a-z0-9][a-z0-9_-]{0,63}$` (`memory/validators.py:21`) |
| Unsafe deserialization | none on untrusted input | `yaml.safe_load` only (`skills/loader.py:62`, `workloads/loader.py:303`); `pickle` only parent-to-child in subprocess IPC (trusted direction; child replies JSON, `skills/_executor_child.py` docstring + `:241`) |
| Path traversal / archive extraction | hardened | `skills/sources.py:_extract_subdir`: member-count cap, per-member and total byte budgets with clamped reads, non-file members rejected, `_safe_target` traversal check, symlink-unlink-before-resolve install dir (BL-161/169/172/190 lineage, re-verified by reading the implementation) |
| Command execution | none unsafe | no `shell=True`, no `os.system`, no `mktemp` in source (`grep` sweep); the one `subprocess.Popen` is the argv-list skill executor child |
| Weak crypto | none | no `hashlib.md5` / `hashlib.sha1` in source (`grep` sweep); EncryptedStore is AES-256-GCM via `cryptography` |
| Blocking calls in async paths | none | no `time.sleep` in source; blocking I/O offloaded per `asyncio.to_thread` convention |
| `BaseException` handling | BL-165 invariant holds | 3 catch sites read in place (`harness/runtime.py:627`, `skills/execution.py:383`, `:484`): thread-boundary transport, cleanup-and-reraise, and guard-state translation after explicit `CancelledError`/`BudgetExceeded` re-raise |
| Non-finite numeric boundaries (the repo's recurring NaN class) | closed at every checked boundary | per-call `ttl_seconds` validated finite + positive in `Namespace.resolve_ttl` (`memory/types.py:60-83`), called by all five adapters and all four `transact` implementations (grep + read of each); S3 metadata via `_safe_float`/`_safe_int`; budget/config constructors per BL-221/231/232 |
| Env var surface | bounded | 6 sites enumerated (inventory section 3); all parent-controlled or explicit key-provider reads |
| Network listeners / webhooks | none exist | library-only; no server code in tree |
| SAST | no new findings | semgrep unavailable in this environment; manual OWASP-aligned pass above; CodeQL `security-extended` runs in CI (push, PR, weekly) as the standing control |

## 3. Phase 3 code-quality findings

### Q-5: `TieredMemoryStore._order` grows without capacity passes

| Field | Value |
|---|---|
| Severity | low |
| CWE | CWE-770 adjacent (resource allocation without limits, process-local) |
| Location | `memory/tiering.py:119-126` (stamp map), pruning only in `demote_to_capacity` (`:290-297`) |
| Evidence | Line read of the full module (Phase 3 deep read; this module postdates the last audit). `write`/`read`-promotion stamp every distinct key; `delete`/`demote` pop; hot-tier expiry does not. The in-code comment "the map stays bounded by the live hot keyspace" holds only when `demote_to_capacity` runs periodically. A wrapper used purely as a read-through composition accumulates one stamp per distinct key ever written, indefinitely. |
| Exploit plausibility | Low: process-local dict growth proportional to distinct keys written between capacity passes; the intended deployment (periodic capacity pass) self-prunes. The repo's own BL-191 precedent treats unbounded growth as a finding even when reachable only off the happy path. |
| Recommended fix | Document the caveat in the class docstring (no behaviour change); an opportunistic prune or LRU stays out of tree per `LIMITATIONS.md` L5. |
| Effort / disposition | S; **documented this pass** (tracked under `BL-239`) |

### Q-6: stale "longer-term fix" wording in `memory/_expiry.py`

| Field | Value |
|---|---|
| Severity | info (comment drift) |
| CWE | n/a |
| Location | `memory/_expiry.py`, `is_expired` docstring ("Validating TTL inputs as finite at the API boundary is the right longer-term fix") |
| Evidence | The validation it calls for already exists: `Namespace.resolve_ttl` rejects non-finite and non-positive `ttl_seconds` (`memory/types.py:75-82`, BL-197) and every adapter + `transact` path routes through it (verified by grep + reading the four `transact` implementations). The docstring reads as if the fix is still pending. |
| Recommended fix | Reword to state the boundary validation landed (BL-197) and the positive-predicate encoding remains as defence in depth for out-of-tree stores. |
| Effort / disposition | S; **fixed this pass** (under `BL-239`) |

### Phase 3 dimensions audited with no finding

| Dimension | Result | Evidence |
|---|---|---|
| New-code correctness (`memory/compaction.py`, `memory/tiering.py`) | no defect found | full line-by-line read; the atomic compactor's absent-target path is correctly CAS-create-gated (`TxnWrite(expected_version=None)` means "must be absent", `memory/store.py:241`); rolling-target, race-undo, and crash-ordering paths match their documented contracts |
| Assertion-free tests | none in substance | AST scan: 1032 test functions, 24 without a literal `assert`; sampled ones are deliberate does-not-raise / frozen-instance tests (e.g. `tests/harness/test_interruption.py:38` raises `AssertionError` manually; `tests/harness/test_bl231_bl232_numeric_config.py:82` documents the does-not-raise intent) |
| Dead code / TODO debt | none | `grep -rn "TODO\|FIXME\|XXX\|HACK"` over source: no hits |
| Unused dependencies | one | F-4 (`logfire`); all other direct deps have import sites |
| Missing timeouts / unbounded retries | none new | subprocess reads are thread-joined with a timeout (`skills/execution.py:388-390`); `RetryPolicy` is finite-validated (BL-231); batch processors were audited in ADRs 0012/0013/0015 and unchanged since |

## 4. Documentation-drift findings (input to Phase 5)

### D-7: post-ADR-0024 drift across four documents

| Field | Value |
|---|---|
| Severity | low (docs only) |
| Location and drift | (a) `README.md` Status paragraph enumerates audit waves through ADR 0023 / `BL-233` only and the Memory capability bullet omits compaction/tiering (and `TransactionalMemoryStore`, in tree since ADR 0014); (b) `docs/README.md` prose ADR enumeration ends at ADR 0022, missing 0023 and 0024; (c) `docs/runbook.md:9` and `:35` still call ADR 0024 "the next audit slot" although ADR 0024 landed as the BL-234/235 capability ADR, and section 1 step 5 cites "(`0020`, `0021`, `0022`)" as the most recent ADRs; (d) `docs/runbook.md` section 8 per-document rows still describe README/CLAUDE/STATUS as current "today" at ADR 0023. |
| Evidence | `grep -ln "ADR 0024" *.md docs/*.md` excludes `README.md` and `docs/README.md`; the runbook lines were read in place; `git diff --stat d704f23..HEAD` confirms `README.md` and `docs/README.md` were not touched by the ADR 0024 wave. |
| Recommended fix | Post-ADR-0024 sweep per the runbook's own section 8 procedure. |
| Effort / disposition | S; **fixed this pass** (Phase 5 commits) |

### D-8: CONTRIBUTING.md mandates DCO sign-off; history carries none and CI does not enforce it

| Field | Value |
|---|---|
| Severity | info (governance documentation vs practice) |
| Location | `CONTRIBUTING.md` "Commit messages and sign-off" ("Sign off every commit ... `git commit -s`"; "Per-commit DCO sign-off is required") |
| Evidence | `git log --format="%h %an%n%(trailers:key=Signed-off-by)" origin/main` over recent history: no `Signed-off-by` trailer on any commit, including the maintainer's own squash merges and the renovate bot's. None of the three workflows runs a DCO check (all read in full this session). Squash-merging routinely drops per-commit trailers, so the requirement as written is neither practiced in the visible history nor enforced. |
| Exploit plausibility | None; legal-process documentation accuracy only. |
| Recommended fix | Maintainer decision: either enforce (DCO check app or CI job) or reword CONTRIBUTING.md to match practice (for example, require sign-off on PR commits and note that squash-merge consolidates them). Not changed in this pass: the choice belongs to the maintainer. |
| Effort / disposition | S; **deferred to backlog** (proposed as `BL-241`) |
