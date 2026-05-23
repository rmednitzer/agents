# Changelog

Material changes by phase. Format follows Keep a Changelog; dates are
ISO 8601. Pre-1.0, so this is phase-based, not semver-tagged.

## [Unreleased] BL-133: skill contract execution isolation (ADR 0016, 2026-05-23)

The long-standing L3 "the gate is defence in depth, not a sandbox"
limitation now has an opt-in in-tree second tier. The default
behaviour is unchanged for every existing caller.

### Added

- `skills.execution` module with the `SkillContractExecutor` Protocol
  (`BL-133`, ADR 0016): how a skill's `contract.py` is loaded and
  evaluated. Two in-tree references:
  - `InProcessSkillContractExecutor` (default): the L1 behaviour
    preserved exactly (import in this interpreter; predicates
    evaluate here).
  - `SubprocessSkillContractExecutor`: load and evaluate in a
    long-lived Python subprocess with `resource.setrlimit` caps on
    CPU time, address space, and open files (POSIX). Crash isolation
    is real; resource exhaustion is bounded; predicate exceptions
    surface as `SkillContractExecutorError` without killing the
    harness. IPC framing: 4-byte length prefix + body; parent->child
    pickled (parent owns the source), child->parent JSON (so a
    malicious bundle cannot RCE the parent).
- `skills._executor_child` module: the subprocess entry point. Reads
  limits + contract path from the environment, applies
  `setrlimit`, imports the contract, ships metadata, then services
  predicate-evaluation requests.
- `SkillContractExecutorError`: distinguishes isolation-layer
  failures (subprocess crashed, IPC framing broke) from
  `SkillManifestError` (the documented "this contract is
  malformed").
- New regression suite `tests/skills/test_bl133_execution_isolation.py`
  (12 tests): Protocol satisfaction; in-process load + evaluate;
  subprocess load + evaluate with IPC round-trip; missing-export /
  malformed-import / predicate-raise / child-crash boundaries;
  loader-level forwarding; default backward-compatibility.
- ADR 0016 (`docs/adr/0016-skill-execution-isolation.md`): the design
  decision and the trust framework.

### Changed

- `Skill` gains an opt-in `_executor` field. `Skill.contract()` uses
  it when set; defaults preserve the legacy in-process call path
  (additive to L1, ADR 0007).
- `discover_skill(executor=None)` and `install_skill(executor=None)`
  forward the executor to the constructed Skill.
- `LIMITATIONS.md` L3 is rewritten: the gate is no longer "no
  isolation" but "default in-process, opt-in subprocess + rlimit,
  out-of-tree container for capability isolation".
- `docs/schema/skill-manifest.json` is unchanged; no manifest-level
  surface changed.

### Documentation

- `docs/runbook.md`: post-ADR-0016 sweep. The "ready" set in section
  4.1 drops the now-resolved `BL-133` row; the open-backlog listing
  in 4.2 mirrors that change. Section 1's "most recent ADRs" hint
  bumps to (`0014`, `0015`, `0016`); section 2.5's audit-ADR
  template recommendation bumps to ADR 0015 (the latest audit
  template); the audit-wave-cadence enumeration on line 31 bumps to
  `0009-0015`. The Phase G sweep checks in section 8 now cite today's
  state: the README check is `0016` + `BL-133`, the CLAUDE check is
  `0007`-`0016`, the STATUS check is `0001-0016`, the SECURITY
  check cites the BL-133 skill execution isolation hardening, and
  the ADR-immutability row covers `0001`-`0016`.
- `SECURITY.md` "Skill contracts" bullet: the in-tree opt-in second
  isolation tier is now named explicitly. The bullet calls out
  `InProcessSkillContractExecutor` (default, backward-compatible),
  the `SubprocessSkillContractExecutor` `resource.setrlimit` caps
  (CPU, address space, open files on POSIX), and the
  length-prefixed parent->child pickle / child->parent JSON IPC
  framing that prevents a malicious bundle from RCEing the parent
  (`BL-133`, ADR 0016). Capability isolation (container / seccomp)
  is restated as the out-of-tree extension point, parallel to the
  CLAUDE.md wording.

## [Unreleased] ADR 0015 deferred close (BL-209-BL-211, 2026-05-23)

The three items ADR 0015 flagged as deferred (M3 / M6 / H5 in the
audit triage), closed as additive follow-ups. No new ADR; folded
into the ADR 0015 record by reference.

### Fixed

- `EncryptedStore` BL-196 multi-key loop catches `KeyError` alongside
  `InvalidTag` (`BL-209`). Defence-in-depth for an out-of-tree
  `IterableKeyProvider` (KMS-backed) that returns a key id from
  `iter_key_ids` which the underlying provider can no longer
  resolve (key revoked between iteration and lookup). The in-tree
  `RotatingKeyProvider` does not remove keys; this is the
  extension point for third-party providers.
- `MarkdownValidatorRuntime` per-line comment tracker (`BL-211`).
  New module-level helper `_double_dash_outside_comment` walks each
  line position-aware; a line that opens or closes an HTML comment
  and carries prose ``--`` is now flagged correctly. Demo workload;
  no production caller, but the validator is the canonical
  contract-binding example.

### Documentation

- `wrap_encrypted` docstring (`BL-210`) extended to flag all three
  content-hash-token Protocols (`CASMemoryStore`,
  `VersionedMemoryStore`, `TransactionalMemoryStore`) as
  intentionally not-forwarded, with the GCM-nonce reason. The
  Protocol-level docs in `store.py` already documented this; the
  factory-level dual now agrees so an operator wrapping a
  capability-rich backend sees at the composition site why the
  decorated store no longer satisfies the version-token Protocols.
- `docs/backlog.md` updated with `BL-209`-`BL-211` (ADR 0015
  deferred-close section).
- `docs/schema/workload-manifest.json` regenerated; no functional
  drift, just the docstring propagation from `wrap_encrypted`.

## [Unreleased] Sixth code audit (ADR 0015, BL-197-BL-208, 2026-05-23)

Twelve additive findings spread across `memory/`, `harness/`,
`skills/`, and `evaluation/`. Each is a class extension of a prior
audit fix (BL-159 / BL-167 / BL-178 / BL-189 / BL-191 / BL-193 /
BL-195) or a novel diagnostic-gap finding. Default behaviour is
unchanged for every valid input; the strict narrowings reject
inputs that previously silently mis-behaved.

### Added

- `Namespace.resolve_ttl(ttl_seconds)` (`BL-197`): one method, one
  validation (finite + positive) for both the namespace default and
  per-call TTL. Each adapter's `_ttl` / `_effective_ttl` now delegates
  here; the five-way duplication identified as M5 in the audit triage
  is closed.
- `BudgetTracker.emit_wall_clock_exceeded(elapsed)` (`BL-202`): the
  runtime's boundary-fallback path emits a `BudgetExceededEvent`
  before the bare raise so every wall-clock terminal raise pairs
  with the audit stream.
- `Redactor.max_depth: int = 64` field (`BL-200`): recursion cap on
  the audit-redaction walker so a cyclic or pathologically deep
  payload cannot crash the audit path.
- `SkillRegistry.routable()` (`BL-208`): filters out
  `lane == "routing"` meta-skills; every dispatcher that iterates
  the registry now uses it.
- `TTLSweeper.failures_total` / `last_error` (`BL-199`): operator-
  visible counters for the sweeper's failure resilience.
- ADR 0015 (`docs/adr/0015-sixth-code-audit.md`): the sixth-audit
  cross-cutting decisions.
- New regression test suites: `tests/memory/test_bl197_bl198_bl199_audit6.py`,
  `tests/harness/test_bl200_bl201_bl202_bl203_audit6.py`,
  `tests/skills/test_bl204_bl205_bl207_bl208_audit6.py`,
  `tests/evaluation/test_bl206_audit6.py` (45 new tests total).

### Fixed

- `Namespace` rejects non-finite `retention_seconds` (NaN / +inf)
  and (via `resolve_ttl`) non-finite per-call `ttl_seconds`
  (`BL-197`, Copilot BL-195 follow-up).
- `RedisStore.mset` short-circuits on an empty batch
  (`BL-198`, BL-178 class extension; parity with
  `RedisStore.mdelete` and `SQLiteStore.mset`).
- `TTLSweeper._run` catches transient `sweep_expired` exceptions
  instead of letting them silently kill the loop (`BL-199`,
  BL-189 class extension).
- `Redactor._scrub` enforces a depth cap so a cyclic or
  pathologically deep payload returns the placeholder instead of
  crashing the emit chain (`BL-200`, audit-path-must-not-crash
  invariant extended).
- `harness.openai_api._decode_lines` yields a placeholder dict for a
  malformed JSONL row (non-dict or undecodable) so iteration
  continues to completion (`BL-201`, BL-189 class extension).
- The runtime emits `BudgetExceededEvent` on the wall-clock
  boundary-fallback path before the bare raise, so every terminal
  raise has a matching audit event (`BL-202`, BL-189 / BL-167
  class extension).
- `run_under_contract` validates the resume state's pending
  approvals BEFORE emitting `ContractStarted`, so an unresolved
  resume cannot leave an orphan event in the audit stream
  (`BL-203`, BL-167 class extension).
- `parse_skill_md` translates PyYAML's `RecursionError` into the
  documented `SkillManifestError` (`BL-204`, BL-173 / BL-191 class
  extension on the manifest-parse leg).
- `MultiDispatcher.__init__` rejects NaN / inf / negative weights
  at the API boundary (`BL-205`, BL-159 NaN-clamp class
  extension).
- `evaluate_trajectory` runs the input-payload validation outside
  the contract try/except, so a fixture error raises as a
  fixture-layer `ValidationError` instead of being mislabelled
  ``output_invalid`` (`BL-206`).
- `InstrumentedDispatcher.dispatch` uses `try/finally` so a
  failing inner dispatch still records stats and emits
  `DispatchObserved` (`BL-207`, BL-189 / BL-167 class extension).
- `KeywordDispatcher` / `EmbeddingDispatcher` / `LLMDispatcher`
  exclude routing-lane meta-skills from their candidate pool, so
  the `dispatcher-skill` (and any operator-installed routing
  meta-skill) cannot be returned as a task recommendation
  (`BL-208`).

### Changed

- Each memory adapter's `_ttl` / `_effective_ttl` helper is now a
  thin delegate to `Namespace.resolve_ttl`. Same call sites; the
  validation now happens at the namespace boundary instead of
  passing through to `expires_at = NaN`.
- `Namespace.retention_seconds` docstring now documents the
  finite-positive contract; the regenerated
  `docs/schema/workload-manifest.json` reflects the new wording.
- `wrap_encrypted` is unchanged; the `BL-196` opt-in multi-key
  fallback is unaffected by this wave.

### Documentation

- ADR 0015 added; `docs/adr/README.md` index extended.
- `STATUS.md` phase tracking row added; `Last reviewed` date
  bumped to 2026-05-23.
- `docs/backlog.md`: new section "Sixth code audit (ADR 0015,
  2026-05-23)" with `BL-197`-`BL-208`.
- `docs/runbook.md`: `Last reviewed` date updated to reflect the
  ADR 0015 audit pass.

## [Unreleased] BL-196: opt-in multi-key legacy fallback on EncryptedStore (2026-05-23)

Runbook 7.4 candidate 4 (the EncryptedStore legacy migration class).
The `BL-181` authenticated legacy fallback was current-key only
(`LIMITATIONS.md` L16): adopting a `VersionedKeyProvider` on a store
sealed by a plain `KeyProvider` could only read values whose
plaintext key matched the current ring version, and a key the
provider had rotated past could not decrypt legacy data without an
out-of-band re-encryption pass through the old store. BL-196 adds
an opt-in lift over a new optional `IterableKeyProvider` Protocol.
Additive: default behaviour is unchanged; every existing call site
is byte-identical.

### Added

- `memory.encryption.IterableKeyProvider` Protocol with
  `iter_key_ids(namespace) -> Iterable[str]`. Optional capability on
  top of `VersionedKeyProvider`; out-of-tree KMS-backed providers
  decide whether to enumerate (they may not want to pay per call).
  `runtime_checkable` so `EncryptedStore` can detect it at
  construction time.
- `RotatingKeyProvider.iter_key_ids` (in-tree reference). Returns
  the key ring in insertion order (seed first, then each `rotate`
  chronologically).
- New `legacy_multi_key: bool = False` kwarg on
  `EncryptedStore.__init__` and `wrap_encrypted`. When `True`, the
  legacy `_unseal` fallback iterates every historical key in the
  ring after the current-key attempt fails. AES-GCM authentication
  still gates each attempt (false-tag probability `2**-128` per key,
  accumulated `N * 2**-128` across the ring), so the multi-key
  fallback never returns a wrong plaintext.
- `tests/memory/test_bl196_multi_key_legacy.py` covers the
  iteration order, the construction guards (both `VersionedKeyProvider`
  *and* `IterableKeyProvider` required for the opt-in), the BL-181
  preservation when off, the historical-key decrypt when on, the
  AES-GCM "no silent wrong value" guarantee under the multi-key
  path, the envelope-still-preferred case, the malformed-value fast
  path, and the `wrap_encrypted` flag forwarding (11 tests).

### Changed

- `EncryptedStore._unseal` legacy fallback path is restructured so
  the current-key attempt is tried first (preserving BL-181), then,
  when `legacy_multi_key=True`, the iteration of `iter_key_ids`
  begins (skipping the current id already tried). The malformed
  fast-path (`len(sealed) < _NONCE_BYTES`) moves above the decrypt
  attempt so a truly-too-short value short-circuits to the original
  envelope error without consuming a ring iteration.

### Documentation

- `memory/encryption.py` module docstring: new "Multi-key legacy
  fallback (BL-196, opt-in)" paragraph.
- `memory/README.md`: extends the key-provider bullet with the
  `legacy_multi_key` opt-in and the IterableKeyProvider Protocol.
- `LIMITATIONS.md` L16: renamed to "current-key only by default" and
  documents the opt-in lift, the AES-GCM bound on false matches, and
  the KMS-provider rationale for keeping the default off.
- `docs/runbook.md` 7.4 candidate 4: marked resolved (referenced
  `BL-196`); the "open question" about AES-GCM tag strength is
  answered affirmatively.
- `docs/backlog.md`: new section "EncryptedStore multi-key legacy
  migration (2026-05-23)" with the `BL-196` line.

## [Unreleased] BL-195: consolidate the expiry-boundary predicate across adapters (2026-05-23)

Runbook 7.4 candidate 1 (the expiry-boundary class). Five pointwise
fixes (`BL-157` / `BL-168` / `BL-177` / `BL-188` / the BL-180
DynamoDB conditions) were the same invariant in different encodings;
they now share one helper. Additive; observable behaviour is
unchanged for every input.

### Added

- `memory/_expiry.py` with `is_live(now, expires_at) -> bool` and
  `is_expired(now, expires_at) -> bool` (`BL-195`). The module
  docstring binds the Python predicate to its SQL counterpart
  (`expires_at < :now` for the expired half) and DynamoDB DSL
  counterpart (`attribute_not_exists(exp) OR exp >= :now` for the
  live half) as documented equivalents, so the SQL / DSL forms stay
  literal where they execute server-side but the invariant has one
  source. Boundary is inclusive at the instant `now == expires_at`.
- `tests/memory/test_bl195_expiry_predicate.py` pins the boundary
  table, asserts `is_live` / `is_expired` are total negations on
  every (now, expires_at) pair, and adds an end-to-end regression on
  `InMemoryStore` that exercises the inclusive boundary instant
  (the BL-188 prior-fix shape, hardened against a future drift back
  to a strict `>`).

### Changed

- `memory.InMemoryStore`, `memory.SQLiteStore`, `memory.S3Store`,
  `memory.DynamoDBStore`: every Python-side liveness check
  (`_live_value` / `_live_item` / `_get_live`, `list_keys`, `scan`,
  `sweep_expired`) now routes through `memory._expiry`. The
  `SQLiteStore.sweep_expired` SQL form keeps its literal
  `expires_at IS NOT NULL AND expires_at < ?` (server-side
  predicate); the docstring is updated to name the helper as the
  binding equivalent. The DynamoDB `_scan_sync` filter is restructured
  from an inline-negated generator into an explicit `is_live` check
  for readability. No observable behaviour change for any input;
  every existing test passes unchanged.

### Documentation

- `memory/README.md`: new bullet under "Documented deviations and
  decorator scope" naming the helper and the inclusive-at-instant
  boundary.
- `docs/runbook.md` 7.4 candidate 1: marked resolved (referenced
  `BL-195`).
- `docs/backlog.md`: new section "Expiry-boundary consolidation
  (2026-05-23)" with the `BL-195` line.

## [Unreleased] BL-180: VersionedMemoryStore on durable adapters + TransactionalMemoryStore (2026-05-23)

See [ADR 0014](./docs/adr/0014-versioned-and-transactional-on-durable-adapters.md).
Closes the BL-124 remainder: brings the MVCC content-hash version
Protocol to the durable network adapters and adds a new
`TransactionalMemoryStore` Protocol for native multi-key transactions.
Additive: defaults reproduce prior behaviour byte-for-byte; `S3Store`
stays excluded for the same reason it does not implement CAS.

### Added

- `memory.RedisStore.read_versioned` / `write_versioned` /
  `delete_versioned` (`BL-180`). WATCH/MULTI/EXEC mirror of
  `compare_and_set` with the precondition switched to a content-hash
  comparison. Persistent contention exhausts the bounded retry budget
  and returns `None` / `False` per the BL-072 best-effort convention.
- `memory.DynamoDBStore.read_versioned` / `write_versioned` /
  `delete_versioned` (`BL-180`). One-round-trip conditional PUT/DELETE
  against a server-stored `ver` attribute (the content-hash of the
  value at write time). `read_versioned` hashes the live `v` for
  path-independence; `write_versioned` and `delete_versioned` use
  `ConditionExpression = "ver = :e AND (attribute_not_exists(exp) OR
  exp >= :now)"`. The `exp >= :now` live boundary matches `_live_item`
  (BL-157 / BL-177 / BL-188 expiry-class).
- `memory.TransactionalMemoryStore` Protocol + `memory.TxnWrite` and
  `memory.TxnDelete` frozen dataclasses (`BL-180`). Atomic multi-key
  version-gated transactions: each operation carries an
  `expected_version` referencing the same content-hash token; the
  transaction commits iff every precondition holds, otherwise it is a
  no-op (`transact` returns `None`). An empty transaction returns `{}`;
  a key in both `writes` and `deletes` is rejected at the contract
  boundary as a caller bug.
- `memory.InMemoryStore.transact` (lock-serialized reference impl;
  `BL-180`).
- `memory.SQLiteStore.transact` (one `BEGIN IMMEDIATE` per call, per-key
  precondition check then per-key apply, `ROLLBACK` on a miss; `BL-180`).
- `memory.RedisStore.transact` (`WATCH(all keys)` / sequential `GET`s /
  hash check / `MULTI` / queued commands / `EXEC`, with `WatchError`
  bounded retry; `BL-180`).
- `memory.DynamoDBStore.transact` (one `transact_write_items` call with
  a per-item `ConditionExpression`; the AWS 100-item hard limit is
  pre-checked at the contract boundary;
  `TransactionCanceledException` whose `CancellationReasons` are all
  `ConditionalCheckFailed` is the no-op signal, any other cancellation
  code propagates; `BL-180`).
- `memory.wrap_acl` gains `_ACLTransactionalMixin` so an ACL-wrapped
  transactional backend keeps `isinstance(wrapped,
  TransactionalMemoryStore)` truthful (BL-156 contract). The guard runs
  per touched key before the inner call; an unauthorised op raises
  `AccessDenied` and aborts the whole transaction (all-or-nothing).
- `tests/memory/test_bl124_versioned.py` is now parametrised over all
  four backends (`inmemory`, `sqlite`, `redis`, `dynamodb`); the new
  `tests/memory/test_bl180_transactional.py` covers the transactional
  Protocol with the same parametrisation. A
  `test_write_versioned_against_legacy_row_without_ver_attribute`
  regression covers the documented DynamoDB legacy-row contract.

### Changed

- `memory.DynamoDBStore._item` now stamps `ver = sha256(value)` on
  every write path (`write`, `mset`, `compare_and_set`,
  `write_versioned`, transactional `Put`). The attribute is consistent
  with `v` by construction; pre-BL-180 rows continue to round-trip
  through the existing `_live_item` path and remain readable via
  `read` / `mget` / `list_keys` / `scan` / `read_versioned`. A
  pre-BL-180 row without `ver` cannot be `write_versioned`-updated
  until a single plain `write()` upgrades it (the documented migration
  contract; `LIMITATIONS.md` L17).

### Documentation

- New `docs/adr/0014-versioned-and-transactional-on-durable-adapters.md`.
- `memory/README.md`: removed the "tracked remainder" note for the
  durable Versioned adapters; added a row for `TransactionalMemoryStore`
  and the `wrap_acl` forwarding + `wrap_encrypted` non-forwarding
  rationale.
- `STATUS.md`: new phase row for `BL-180` / ADR 0014.
- `LIMITATIONS.md`: new L17 documenting the DynamoDB legacy-row
  migration contract; the L5 / L12 references unchanged.

## [Unreleased] CI gate hardening: dependency-audit env + disputed pyjwt CVE (2026-05-20)

A CI-policy fix folded into PR #47: the `dependency-audit` job started
failing on every run (including `main`) once `uvx pip-audit` defaulted
its dry-run env to Python 3.11. Build-pipeline change only; no code
or library surface affected.

### CI

- `.github/workflows/ci.yml`: the `dependency-audit` job now invokes
  `uvx --python 3.12 pip-audit ...`, matching the project's
  `requires-python = ">=3.12"`. Without the pin, uvx picked 3.11 and
  the marker `python_version < "3.12"` on `backports.tarfile` (a
  transitive of `jaraco-context`) became true, pulling an unpinned
  `>=` constraint that pip refuses under `--require-hashes`. The audit
  env must match what is actually installed. (`BL-194`)
- Same step adds `--ignore-vuln PYSEC-2025-183` (CVE-2025-45768) with
  an inline rationale: a maintainer-disputed advisory against `pyjwt`
  with no fix version published, reachable here only as a deep
  transitive (`mcp` -> `pydantic-ai-slim` -> `pydantic-ai`); no JWT
  code path is exercised. Revisit trigger documented in the workflow
  comment (advisory withdrawal, replacement CVE, or a hardened pyjwt
  default). (`BL-194`)

## [Unreleased] Approval-resume argument binding (2026-05-20)

PR #46 (`a511760`). A post-ADR-0013 security fix to the runtime
adapter's approval-resume path: a stale resolved approval for one set
of arguments could satisfy a different call to the same tool on
resume. Additive (the new match condition is a strict narrowing of the
prior over-permissive one); regression test added.

### Security

- `harness.runtime._resolved_decision` now binds an in-progress
  approval lookup by the full `(tool, arguments)` tuple, not by `tool`
  alone: an approval previously granted for
  `risky({"path": "approved.txt"})` no longer authorises a fresh
  `risky({"path": "victim.txt"})` after a pause. The default
  `HarnessToolGuard` mints a new `interruption_id` per check, so the
  id is not a stable cross-pause binding key on its own; the docstring
  records this. Regression test:
  `tests/harness/test_runtime_adapter.py::test_gate_resume_does_not_reuse_stale_approval_for_new_arguments`.
  Authorization-boundary fix on the L1 / L2 approval-resume path
  (`BL-001`, `BL-002`). (`BL-193`)

## [Unreleased] Fifth code audit: additive hardening (2026-05-19)

See [ADR 0013](./docs/adr/0013-fifth-code-audit.md). A fifth in-depth
audit targeting the classes the prior audits fixed pointwise and the
paths the recent major dependency bumps exercise. Additive with
regression tests; defaults reproduce prior behaviour for every
non-adversarial input.

### Fixed

- `InMemoryStore` / `SQLiteStore` `list_keys` and `scan` now use the
  same `now <= expires_at` live boundary as `read` / `sweep_expired`;
  a key at the exact expiry instant that `read` still returns is no
  longer missing from a listing for one tick (the read-vs-CAS boundary
  class, unfixed for the listing paths of the in-tree reference
  adapters; BL-168's fix comment, which wrongly asserted agreement, is
  corrected). (`BL-188`, ADR 0013)
- `OpenAIBatchProcessor.results` surfaces a structured `error.code`
  for an output-file row with `response: null` and an `error`
  (previously yielded an uninformative `http_None`, dropping the
  diagnostic on a billing-relevant bulk path). (`BL-189`, ADR 0013)
- `_balanced_spans` caps the recorded span list at `_MAX_SPANS`, so a
  bracket-heavy untrusted body cannot amplify into an unbounded list of
  index pairs (a memory axis the BL-173/182 parse-work bounds did not
  cover); overflow degrades to the existing malformed-input contract.
  (`BL-191`, ADR 0013)
- `scripts/check_run_records.py` validates `--registry` values as
  canonical lowercase 64-hex at load; a non-canonical registry is now
  a clear invocation failure (exit 2) naming the bad keys instead of
  making the gate silently unsatisfiable. (`BL-192`, ADR 0013)

### Security

- `LocalSkillSource.fetch` clears `dest/<name>` through the shared
  symlink-safe `_prepare_install_dir` (defence in depth and a clean
  `SkillLoadError` instead of an unhandled `OSError` on a pre-existing
  symlink; the BL-172 "twin", finally propagated). Not an escape;
  consistency / robustness hardening. (`BL-190`, ADR 0013)

### Documentation

- ADR 0013 added; the ADR index gains the missing `0012` row and the
  new `0013` row; `docs/backlog.md` tracks `BL-188`-`BL-192`; CLAUDE.md
  ADR enumeration extended to `0012`/`0013`.

## [Unreleased] Cross-repo review: run provenance + Anthropic capabilities (2026-05-17)

See [ADR 0012](./docs/adr/0012-run-provenance-and-anthropic-capabilities.md).
Additive: a new opt-in `record_sink` keyword on `run_under_contract`,
three new harness modules (`provenance`, `anthropic_api`, `openai_api`),
the `scripts/check_run_records.py` gate, and two optional extras
(`anthropic`, `openai`); defaults reproduce prior behaviour.

### Added

- `harness.RunRecord` / `contract_digest` / `verify_run_record` and a
  `record_sink` keyword on `run_under_contract`: a schema-versioned,
  self-attesting record stamped at the run's terminal point with the
  in-process digest of the contract that actually enforced it (not
  reconstructed from git, the explicit divergence from the `sentinel`
  provenance approach). `scripts/check_run_records.py` re-validates a
  persisted corpus with hard errors only (no warn-and-pass tier).
  `docs/schema/run-record.json` is gen-schema guarded. (`BL-185`,
  ADR 0012)
- `harness.AnthropicBatchProcessor` (Message Batches: async bulk at
  50% token price, dependency-injected client, lazy `from_env`) and
  `harness.cache_control_system` (prefix-stable prompt-cache block),
  behind a new optional `anthropic` extra; the module imports and
  type-checks with the SDK absent. (`BL-186`, ADR 0012)
- `harness.OpenAIBatchProcessor`: the OpenAI counterpart (JSONL
  upload, batch create, JSONL output/error decode), behind a new
  optional `openai` extra, same injected-client + lazy `from_env`
  design. `OpenAIBatchRequest.model` is required (no guessed default).
  (`BL-187`, ADR 0012)

## [Unreleased] Third audit + L3 capability wave (2026-05-17)

See [ADR 0011](./docs/adr/0011-third-audit-and-l3-capability-wave.md).
All changes are additive to the L1 Protocols (new optional keywords /
modules / side-by-side Protocols; defaults reproduce prior behaviour).

### Added

- `memory.EnvKeyProvider` / `memory.FileKeyProvider` (single key,
  base64/hex/raw, stdlib only); the `memory.VersionedKeyProvider`
  Protocol and `memory.RotatingKeyProvider` reference; `EncryptedStore`
  writes a rotation-safe key-id value envelope over a versioned
  provider so a rotation does not strand prior ciphertext (a plain
  `KeyProvider` keeps the exact prior on-disk format). (`BL-111`)
- `memory.AttributeACL` / `memory.AttributeRule` (attribute-based
  access decided per call) and a `harness` `AccessDenied` event
  (exported as `AccessDeniedEvent`) emitted by `ACLStore` / `wrap_acl`
  before raising when the optional audit surface is supplied.
  (`BL-122`)
- `memory.SemanticMemoryStore` extension Protocol + `SemanticHit` and
  `memory.InMemorySemanticStore` (with `memory.Embedder`), a
  deterministic in-tree vector store reusing the BL-110
  `HashingEmbeddingProvider`. (`BL-131`)
- `memory.VersionedMemoryStore` extension Protocol
  (`read_versioned` / `write_versioned` / `delete_versioned`, a
  content-hash MVCC token) with `InMemoryStore` and `SQLiteStore`
  reference impls. (`BL-124`)
- A top-level `evaluation/` component (`evaluate_dispatch` P@1 / MRR,
  `evaluate_trajectory`, golden-set loader, metrics), the in-tree
  golden set, `scripts/eval.py`, and a blocking CI `evaluation` job in
  the `ci-success` aggregate (mypy and the coverage target now include
  `evaluation`). (`BL-130`)
- `run_under_contract(..., parent_span_id=...)` for a correlated
  nested-run span tree (None preserves the prior flat behaviour).
  (`BL-176`)
- ADR 0011.

### Fixed

- A pre-existing `dest/<name>` symlink let `GitHubSkillSource` /
  `MarketplaceSkillSource` escape the install directory (it resolved
  before clearing the link); a hardened `_prepare_install_dir` unlinks
  the link first and asserts containment (the network-source twin of
  `BL-169`). (`BL-172`)
- `_balanced_spans` was O(n^2) on nested `[[[...]]]` model output
  (per-close substring slices); now O(1) index pairs with a capped
  lazy parse, and `RecursionError` from a deep span is contained in
  the extractor and the LLM / skill-based dispatcher boundary
  (DispatchError contract kept). (`BL-173`)
- `compose_contracts` governance keeps the strictest predicate (HARD
  over SOFT) on a name collision, not first-occurrence (the governance
  analogue of `BL-166`; a reviewed HARD veto was silently downgraded).
  (`BL-174`)
- A postcondition retry directive re-recorded every predicate into the
  `DriftMonitor`; postcondition drift is now recorded exactly once per
  run. (`BL-175`)
- `DynamoDBStore.compare_and_set` (match branch) / `compare_and_delete`
  gate on `exp >= :now`, matching `_live_item`'s live boundary (was
  `> :now`, CAS-absent at the exact expiry instant). (`BL-177`)
- `SQLiteStore.mset` / `mdelete` of an empty batch is an early no-op
  (was taking the write lock for nothing). (`BL-178`)

### Changed

- `RetryPolicy` docstring corrected: token/step usage is charged from
  the final attempt only (PydanticAI raises without partial usage on a
  failed run); the gap is tracked. (`BL-179`, `LIMITATIONS.md` L15)

### PR #28 review follow-ups

- `BL-181`: authenticated legacy-ciphertext fallback so adopting a
  `VersionedKeyProvider` on a plain-provider store stays readable
  (AES-GCM authenticated; migration contract in `LIMITATIONS.md` L16).
- `BL-182`: `first_json_array` bounds parse *work* (oversized-span
  skip + cumulative byte budget, `continue` not `break`), not candidate
  count, so a valid array after many leading bracket fragments is found
  while the BL-173 DoS bound holds.
- `BL-183`: `evaluate_trajectory` classifies `paused` (ResumableState)
  and `approval_denied` outcomes instead of mis-scoring/aborting;
  `wrap_acl` forwards `VersionedMemoryStore`; `InMemorySemanticStore`
  query is safe under concurrent vector removal.
- `BL-184`: review-polish trio: `_balanced_spans` docstring matches the
  current byte-budget constants; `_prepare_install_dir` clears a
  pre-existing regular file (not only a directory); `_decode_key`
  surfaces a clear `ValueError` for a malformed base64/hex/utf-8 key.

### Documentation

- ADR 0011; `docs/adr/README.md`; backlog statuses (`BL-111`, `BL-122`,
  `BL-124`, `BL-130`, `BL-131` resolved; `BL-172`-`BL-179` added;
  `BL-180` added as the BL-124 multi-key remainder);
  `STATUS.md` / `LIMITATIONS.md` refreshed (L5 narrowed, L6 narrowed,
  L15 added); component READMEs and `CLAUDE.md` layout (the
  `evaluation/` component).

## [Unreleased] L3 default-path wiring + audit wave (2026-05-17)

See [ADR 0010](./docs/adr/0010-l3-default-path-wiring-and-audit-wave.md).
All changes are additive to the L1 Protocols (new optional keywords /
modules; defaults reproduce L1/L2 behaviour).

### Added

- Default-path wiring on `run_under_contract` (all opt-in): `skill_contracts`
  composition (`BL-100`), `drift_monitor`/`drift_threshold` with a new
  `DriftThresholdCrossed` event (`BL-101`), recovery directives via
  `RecoveryOutcome.directive` (`BL-102`), and run-scoped `lifecycles`
  (`BL-104`).
- `skills.dispatchers.default_dispatcher`: the recommended instrumented,
  cheap-first chain in one call (`BL-103`).
- `skills.HashingEmbeddingProvider`: a deterministic, dependency-free
  `EmbeddingProvider` so `EmbeddingDispatcher` works with no vendor
  (`BL-110`).
- `ActionBudget.max_cost_usd` / `max_tokens_per_tool` /
  `max_wall_clock_seconds_per_tool`; `BudgetTracker.consume_cost`,
  `snapshot`, and per-tool token/second attribution; `initial_*`
  seeding for a cumulative resume (`BL-123`, `BL-154`).
- `harness.RetryPolicy` and `PydanticAIRuntime(retry_policy=...,
  soft_reject_as_error=...)` (`BL-136`, `BL-137`).
- `skills.MarketplaceSkillSource`, a `SignatureVerifier` hook on the
  network sources, and one shared hardened download/extract path
  (`BL-112`).
- `workloads.load_workload_from_entry_point` (installed-package
  workloads via `[project.entry-points]`) (`BL-121`).
- `agents run --json` and `agents skills install <name> --from <src>`
  (`BL-125`); the CLI honours a model-free manifest dispatcher
  (`BL-161`).
- `memory.wrap_acl` / `memory.wrap_encrypted`: decorators that forward
  the extension Protocols the wrapped backend supports, truthfully
  (`BL-156`).
- Governance/release: tree-wide `REUSE.toml` + `LICENSES/Apache-2.0.txt`
  with a `reuse lint` CI gate (`BL-152`); a blocking `dependency-audit`
  CI job (`BL-150`); `docs/releasing.md` and `.github/workflows/release.yml`
  (`BL-151`); ADR 0010.

### Fixed

- `HarnessToolGuard`: a SOFT governance failure now returns REJECT/SOFT
  instead of APPROVE (it was a silent no-op; the runtime's soft-reject
  path was dead). (`BL-163`)
- A raising `RecoveryHandler` is contained (`recovered=False`,
  continue) instead of aborting a soft path. (`BL-164`)
- `PydanticAIRuntime.run` re-raises `CancelledError`/`BudgetExceeded`
  before consulting guard state. (`BL-165`)
- `compose_contracts` keeps the strictest predicate (HARD over SOFT) on
  a name collision, not first-occurrence. (`BL-166`)
- `MemoryAudit` rejects reserved base-event keys (e.g. `namespace`) at
  construction, not mid-run. (`BL-167`)
- `SQLiteStore.sweep_expired` uses strict `<`, consistent with
  read/list/scan. (`BL-168`)
- `LocalSkillSource` copies regular files only and refuses a symlink
  (was dereferencing into the bundle). (`BL-169`)
- `S3Store.scan` / `DynamoDBStore.scan` page past non-terminal empty
  pages instead of falsely signalling exhaustion; `S3Store.list_keys`
  pushes the prefix server-side; `SQLiteStore` batch ops are atomic;
  `SkillRegistry` `name@version` parses via `rpartition`; non-file
  archive members are rejected; per-member reads clamp to the remaining
  budget; `DynamoDBStore` `exp` is float seconds. (`BL-157`, `BL-161`,
  `BL-170`)

### Changed

- `EncryptedStore.read`/`write` route through shared `_seal`/`_unseal`
  helpers (behaviour unchanged); `install_skill`/registry gain an
  `allow_contract` passthrough.

### Documentation

- ADR 0010; `docs/releasing.md`; backlog statuses (`BL-100`-`BL-104`,
  `BL-110`, `BL-112`, `BL-121`, `BL-123`, `BL-125`, `BL-136`, `BL-137`,
  `BL-150`-`BL-152`, `BL-154`, `BL-156`, `BL-157`, `BL-161` resolved;
  `BL-163`-`BL-171` added); `STATUS.md`/`LIMITATIONS.md` refreshed
  (L4, L5, L9, L10, L12-L14). Dispatcher-count erratum: "seven routers
  plus an InstrumentedDispatcher wrapper" (ADR 0009 said "eight",
  internally contradictory; ADRs are immutable, corrected forward).
  `docs/runtime-providers.md` PydanticAI doc URLs and shifted line
  citations corrected.

## [Unreleased] Skill bundles: shell and routing (2026-05-17)

### Added

- `skills/shell/`: a skill for authoring robust, safe Bash and running
  commands reliably on local and remote machines (SSH-first; a decision
  ladder of direct exec, `setsid`/`systemd-run`, and `expect`, with no
  screen-scraping rung). Ships references and a ShellCheck-clean
  Bash skeleton asset.
- `skills/dispatcher-skill/`: the versioned routing skill whose body is
  the dispatch prompt for `SkillBasedDispatcher`. Makes the recommended
  default dispatcher composition in ADR 0006 runnable as written
  (previously the composition referenced a skill that did not ship).

## [Unreleased] Code audit: correctness, security, documentation (2026-05-17)

### Fixed

- `skills.embeddings.cosine_similarity`: a non-finite component (NaN or
  inf, e.g. a buggy provider or adversarial skill text) returned NaN,
  which `min`/`max` clamping turned into confidence `1.0`, sorting a
  poison skill to the top of embedding dispatch. Now returns `0.0`.
  (`BL-159`)
- `skills.dispatchers._json.first_json_array`: the bracket scanner was
  O(n^2) on adversarial model output (a megabyte of `[` hung the
  dispatcher). Rewritten as a single linear pass. (`BL-159`)
- LLM and skill-based dispatchers: a JSON `confidence: true` was
  accepted (`isinstance(True, int)`) and coerced to `1.0`; booleans are
  now rejected. (`BL-159`)
- `memory.EncryptedStore` / `memory.ACLStore`: validate keys before any
  keyed operation, per the `MemoryStore` Protocol. For `EncryptedStore`
  this also prevents a `::` key from colliding AAD across keys.
  (`BL-159`)

### Changed

- `harness.redaction.Redactor` now scrubs every event field, not only
  dict-valued ones, so a secret-shaped or over-long value in a
  top-level string or list field is also redacted. Strict superset of
  the prior behaviour. (`BL-159`)

### Security

- `SECURITY.md`: added the "Untrusted content and prompt injection"
  posture (tool results, MCP output, skill bodies, memory values, and
  model output are untrusted; the authority boundary is the harness,
  not the prompt) and documented the out-of-tree workload code-execution
  boundary and the wall-clock-watchdog caveat. (`BL-139` resolved)

### Documentation

- ADR 0009 records the audit's cross-cutting decisions and the ADR
  0005/0006 factual errata (immutable ADRs noted forward, not edited).
- `LIMITATIONS.md` L10-L14 (budget non-cumulative across resume;
  watchdog preempts only at await; decorators do not forward extension
  Protocols; DynamoDB integer-second TTL; out-of-tree workload code
  execution). `docs/backlog.md`: `BL-154`-`BL-162` added; provenance
  date refreshed. `BL-162` / ADR 0009 section 5 record the ADR 0008
  section 4 follow-up: `main` branch protection must be repointed from
  the stale `test` context to `ci-success` (a Settings change, not a
  file; repoint, do not relax the gate).
- Fixed factual drift: README dispatcher count (eight, not "seven");
  `docs/runtime-providers.md` stale line citations; the `BL-130` ->
  `BL-134` reference in `harness.redaction`; `workloads.manifest`
  "Phase 5" tense; the "five reference dispatchers" docstrings; the
  watchdog "preemptive" wording. `CLAUDE.md`: the markdown `--`/em-dash
  rule now exempts code spans (it previously contradicted its own docs).

## [Unreleased] L3 Tier 0: security hardening and roadmap (2026-05-17)

### Added

- `harness.redaction`: `Redactor` and `RedactingSink` scrub sensitive
  argument names, secret-shaped values, and over-long scalars from
  events before they reach a sink. Exported from `harness`. (`BL-134`)
- `skills.sources.GitHubSkillSource`: keyword-only `sha256`,
  `max_download_bytes`, `max_members`, `max_file_bytes`,
  `max_total_bytes` bound a hostile or corrupt archive and optionally
  verify the tarball digest. (`BL-112`, partial)
- `skills.loader.discover_skill`: `allow_contract` parameter; gates
  execution of a skill's `contract.py`. (`BL-133`, partial)
- Governance docs: `STATUS.md`, `LIMITATIONS.md`, `CHANGELOG.md`,
  `docs/adr/README.md`, expanded `CONTRIBUTING.md`. (`BL-153`)
- ADR 0008; CodeQL workflow; Python 3.13 added to the CI test matrix.
- `docs/backlog.md`: restructured into priority tiers; `BL-130`-`BL-153`
  added from the deep analysis and validated against primary sources.

### Changed

- `skills.sources.install_skill` now defaults `allow_contract=False`: an
  installed bundle's `contract.py` is not executed unless the caller
  opts in. Intentional secure default at the network trust boundary;
  the L1 `discover_skill` default is unchanged. See ADR 0008.

### Security

- Closed the unbounded-download and decompression-bomb exposure in the
  GitHub skill source and the auto-execution of untrusted skill
  contracts. Closed plaintext secret leakage into event sinks. Residual
  risk (gate is not a sandbox) is recorded in `LIMITATIONS.md` L3.

## [L2] Implementation wave (2026-05-16)

Guard and budget wiring into the PydanticAI adapter, durable memory
backends (Redis, SQLite, S3, DynamoDB), observability surface,
contract composition, recovery handlers, JSD drift, skill install.
Delivered as PR #20 (merge `af1df9d`); follow-ups PR #21, PR #22
(coverage gate at 94%). All `BL-001`-`BL-090` resolved. See ADR 0007
and `docs/backlog.md`.

## [L1] Framework (2026-05-16 and earlier)

Contract surface and enforcement, action budgets, memory namespace
contract with `InMemoryStore`, workload bundles and loader, skills and
the dispatcher framework, the `Runtime` Protocol with a PydanticAI
default. See ADR 0001-0006.
