# Changelog

Material changes by phase. Format follows Keep a Changelog; dates are
ISO 8601. Pre-1.0, so this is phase-based, not semver-tagged.

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
