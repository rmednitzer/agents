# CLAUDE.md

Guidance for Claude (and Claude Code) when working in this repository.

## Repository purpose

`agents` is an infrastructure repository for agentic workloads: the runtime environment, skill definitions, orchestration harness, and memory backends that agents depend on. It is the operational substrate, not a single agent.

Scope:
- Workloads: individual agent missions and their entry points.
- Skills: reusable capability definitions, packaged as markdown with optional bundled assets, following the Anthropic skill convention.
- Harness: orchestration, sandboxing, execution control, tool-use contracts.
- Memory: state persistence, retrieval, schema, lifecycle.

Out of scope: model training, dataset curation, model serving infrastructure.

## Architecture

The repo treats agents as composable. A workload is a thin entry point that loads skills, runs inside the harness, and interacts with memory through declared interfaces. Boundaries between these layers are explicit and enforceable.

Component contracts:
- A workload declares: skills it loads, harness it targets, memory namespace, exit conditions.
- A skill declares: trigger description, inputs, outputs, side effects, dependencies.
- The harness enforces: sandbox boundaries, tool-use authorization, action budgets, observability.
- Memory exposes: typed read/write per namespace, retention, isolation, lineage.

## Layout

```
agents/
  agents/      operator CLI package (python -m agents)
  workloads/   individual agent workloads + loader
  skills/      reusable skill definitions, registry, dispatchers
  harness/     orchestration and execution control
  memory/      memory backends, schemas, retrieval
  evaluation/  behavioural regression gate (dispatch P@1/MRR, trajectory)
  tests/       test suite, mirrors source layout
  docs/        architecture, ADRs, backlog, generated JSON Schema
  scripts/     operational and developer scripts
```

The L1 framework, the full L2 wave, the L3 default-path-wiring + audit
wave, and the third-audit + L3 capability wave are implemented on
`main`; see `docs/backlog.md` (line-item tracker) and the cross-cutting
ADRs `0007` (L2), `0008`/`0009` (L3 entry + first audit), `0010`
(default-path wiring, second audit, governance maturity), `0011`
(third audit; key providers, attribute-based ACL, MVCC tokens,
semantic memory, the evaluation gate), `0012` (run-provenance records,
optional provider batch capabilities), `0013` (fifth audit;
read-vs-listing expiry boundary, the JSON span-list memory ceiling,
provider-batch and provenance-gate hardening), `0014` (`BL-180`:
`VersionedMemoryStore` on the durable adapters and the new
`TransactionalMemoryStore` Protocol for atomic multi-key transactions),
`0018` (eighth audit; `JsonlSink` UTF-8 write-side encoding,
subprocess child partial-header EOF treatment, `BudgetTracker`
finite/non-negative validation on caller-fed floats, `MultiDispatcher`
per-member failure containment), `0019` (ninth audit; `MultiSink`
per-sink failure containment on the audit fan-out side, the BL-222
ensemble-side guarantee generalised to the sequential sink fan-out),
and `0020` (tenth audit; S3 user-metadata trust-boundary parsing via
`_safe_float` / `_safe_int` applied at every metadata-read site to
generalise the BL-159 / BL-201 / BL-205 / BL-215 / BL-217 / BL-221
invariants to the S3 boundary, plus `BoundedS3Store.evict_to_capacity`
per-key DELETE containment generalising the BL-222 / BL-223 fan-out
failure invariant and the BL-202 / BL-167 audit-vs-raise parity to the
new BL-225 sequential-DELETE path), and `0021` (eleventh audit;
`BL-228` `RoutingChainDispatcher` per-link failure containment, the
BL-222 / BL-223 / BL-227 fan-out class on the sequential cheap-first
chain, a raising link falling through while `BaseException` still
propagates, and `BL-229` S3 metadata-scan HEAD not-found containment
via `S3Store._head_metadata`, so a LIST-then-HEAD concurrently-deleted
object no longer crashes `sweep_expired` / `evict_to_capacity`,
closing the two open ADR 0020 revisit triggers; the DynamoDB
`float(exp)` shape was left unchanged with a documented rationale,
`BL-230`, since the `N` type is server-validated to a finite range),
and `0022` (twelfth audit; non-finite numeric configuration
validation, generalising the BL-159 / BL-205 / BL-221 / BL-226 NaN
class from the value/data boundaries to the numeric *configuration*
boundaries that are the peers of the `Namespace.retention_seconds`
boundary BL-197 hardened: `BL-231` adds construction-time finiteness /
sign validation to `ActionBudget` and `RetryPolicy`, which had none, so
a `NaN` / `+inf` limit can no longer silently disable a `consumed >
limit` ceiling, the dual of BL-221; `BL-232` adds a `math.isfinite`
conjunct to the `MCPServerSpec.timeout_seconds` and
`TTLSweeper.interval_seconds` `<= 0` guards, since `NaN <= 0` /
`+inf <= 0` are both False, so a `NaN` sweep interval can no longer
drive a no-delay busy-sweep), and `0023` (thirteenth audit; `BL-233`
extends the BL-222 / BL-223 / BL-227 / BL-228 fan-out per-member
failure containment class from `evict_to_capacity` to the sibling
periodic-TTL-sweep path: `S3Store._sweep_sync` and
`DynamoDBStore._sweep_sync` now contain a per-item network DELETE
`Exception` so one transient `SlowDown` / `ProvisionedThroughputExceeded`
/ network blip no longer aborts the whole sweep pass and strands every
later expired item, the inspection step staying fail-loud and only the
idempotent DELETE best-effort, closing the question ADR 0020 / 0021 /
0022 deferred from the BL-229 `_head_metadata` scope), and `0024`
(`BL-234` / `BL-235`, closing `BL-135`: memory compaction,
summarisation, and hot/cold tiering as drivers and compositions over
the existing Protocols; the `Summarizer` Protocol +
`TruncatingSummarizer` deterministic reference, the `MemoryCompactor`
driver with version-gated atomic compaction over the ADR 0014
`VersionedMemoryStore` + `TransactionalMemoryStore` surface and an
explicit `atomic=False` best-effort opt-in, and `TieredMemoryStore`,
the hot/cold two-tier composition behind the plain `MemoryStore`
surface with CAS-guarded promotion, hot-first writes with cold
invalidation, cold-first deletes, version-gated demotion, and
BL-212-ranked `demote_to_capacity`; no new store Protocol, no adapter
changes, LRU and model-quality summarisation stay out of tree), and
`0025` (fourteenth audit; the full-pass engagement protocol with
evidence under `audit/`: `BL-236` removes the stale `PYSEC-2025-183`
pip-audit suppression after its revisit trigger fired, `BL-237` adds
the `uv lock --check` lockfile-freshness gate to the
`dependency-audit` job, `BL-238` drops the unused direct `logfire`
declaration with the resolved graph unchanged, `BL-239` fixes the
`TieredMemoryStore` stamp-map and `_expiry` post-BL-197 docstrings;
no runtime code finding, the ADR 0024 modules' first audit coverage
clean, `BL-240` / `BL-241` proposed for maintainer decision).

## Conventions

Language: Python, pinned at `requires-python = ">=3.12"` in pyproject.toml. CI runs on 3.12.

Formatting: ruff (lint + format) and mypy (strict) are configured in pyproject.toml. Run `make check` (lint + type-check + test) before pushing.

Additive-to-L1 rule (ADR 0007): L2 and later changes are additive to the L1 Protocols. Use new optional keyword parameters (defaults preserving L1 behaviour), new modules, or new Protocols beside the existing ones. Do not remove or change an L1 import path or signature. Surface configuration errors at load time, not mid-run.

Documentation style: no em-dashes and no `--` as prose punctuation, that is, outside HTML comments and inline or fenced code spans (a backticked flag like `--all-extras` or a value like `cov-fail-under=94` is fine; the rule targets the punctuation dash, not literal code). The repo's own markdown rule, dogfooded by `workloads/_example`. Use commas, colons, or parentheses instead.

Naming:
- Workloads: `workloads/<purpose>/` (snake_case, describes mission, not technology).
- Skills: `skills/<skill-name>/SKILL.md` plus optional `references/`, `scripts/`, `assets/`.
- Harness modules: `harness/<concern>.py`.
- Memory backends: `memory/<backend>/` with a thin adapter in `memory/__init__.py`.

Dates and units: SI units, ISO 8601 dates (YYYY-MM-DD), 24h time. Default timezone UTC unless explicit.

Documentation tone: direct, technical, no marketing voice. Each component has a README.md explaining purpose, contract, and example usage.

## Adding components

New workload:
1. Create `workloads/<name>/` with `README.md`, `__main__.py`, `manifest.yaml` (skills loaded, harness target, memory namespace, exit conditions).
2. Add tests under `tests/workloads/<name>/`.
3. Document in `docs/workloads/<name>.md` if non-trivial.

New skill:
1. Create `skills/<name>/SKILL.md` with YAML frontmatter (name, description) per Anthropic convention.
2. The description must be specific enough that a router selects it precisely; avoid generic language.
3. Bundle references under `skills/<name>/references/` if static knowledge is needed.
4. Add invocation tests under `tests/skills/<name>/`.

New harness module:
1. Add `harness/<concern>.py`; document the contract it enforces in a module docstring at the top.
2. Tests under `tests/harness/`.
3. If it changes an existing contract, write a short ADR under `docs/adr/`.

New memory backend:
1. Add `memory/<backend>.py` implementing the `MemoryStore` Protocol (single module; the existing adapters are `memory/{inmemory,sqlite,redis,s3,dynamodb}.py`).
2. Import any third-party driver lazily inside `__init__` with a clear error naming the extra; declare the extra in `[project.optional-dependencies]`. The package must import and type-check with the driver absent.
3. Reuse `memory._audit.MemoryAudit` for the optional `sink`/`base_event_fields` surface; offload blocking I/O via `asyncio.to_thread`; validate keys with `memory.validators`.
4. Implement only the extension Protocols the backend can honour (Batch/Scan/ContentAddressable/CAS/Sweepable/Semantic/Versioned); do not fake unsupported ones.
5. Tests under `tests/memory/`, using an in-process double (`fakeredis`, `moto`) guarded by `pytest.importorskip`. Document retention, isolation, and any semantics deviation in `memory/README.md` and the module docstring.

## Quality bar

- Every public function has a type signature.
- Every component has a one-paragraph README explaining its contract.
- Every directory under `workloads/`, `skills/`, `harness/`, `memory/` has a README.md.
- Tests are not optional for harness and memory; advisory for workloads and skills.
- CI (lint, type-check, test) must pass before merge. `python scripts/gen_schema.py --check` guards JSON Schema drift and runs in the suite; regenerate with `make schema` after changing a manifest model.
- Changes stay additive to the L1 Protocols (see Conventions / ADR 0007).

## Risk

This repo defines authority boundaries between humans, agents, and tools. Treat changes to the harness or memory contracts as high-impact:
- Run the full test suite.
- Document the contract change in an ADR under `docs/adr/`.
- State blast radius in the PR description (which components, which contracts, rollback path).

## Build and test

Uses `uv`. Set up: `uv sync --all-extras` (installs every optional backend plus test doubles so CI exercises all adapters).

- `make test` runs pytest.
- `make lint` runs `ruff check`.
- `make type-check` runs `mypy agents harness memory workloads skills evaluation`.
- `make check` runs lint + type-check + test (run before pushing).
- `make schema` regenerates `docs/schema/*.json` from the Pydantic models.
- CI also runs `uvx reuse lint` (REUSE 3.x compliance via `REUSE.toml`),
  a blocking `dependency-audit` job (`uv lock --check` lockfile
  freshness, then `pip-audit` over the exported lockfile, no advisory
  suppressions), a blocking `secret-scan` job (gitleaks with the
  `.gitleaks.toml` allowlist, BL-240), and a blocking `evaluation` job
  (`python scripts/eval.py`, the BL-130 dispatch P@1/MRR regression
  gate), all in the `ci-success` aggregate gate.

The PydanticAI runtime is tested deterministically with `TestModel`/`FunctionModel` (no network or API keys). Optional-backend tests skip cleanly when their driver is absent. Provider selection and credentials are documented in `docs/runtime-providers.md`.

## Status and limitations

Phase and document maturity: `STATUS.md`. Explicit scope boundaries and known gaps: `LIMITATIONS.md`. Material changes by phase: `CHANGELOG.md`. Versioning, release, and operations policy: `docs/releasing.md`. ADR index: `docs/adr/README.md`. The L3 roadmap is tiered in `docs/backlog.md`; cross-cutting decisions are ADR 0007 (L2), ADR 0008/0009 (L3 entry + first audit), ADR 0010 (default-path wiring, second audit, governance and release maturity), ADR 0011 (third audit; key providers, attribute-based ACL, MVCC tokens, semantic memory, the evaluation gate), ADR 0012 (run-provenance records, optional provider batch capabilities), ADR 0013 (fifth audit; the read-vs-listing expiry boundary and provider-batch/provenance-gate hardening), ADR 0014 (`BL-180`: `VersionedMemoryStore` on the durable adapters + `TransactionalMemoryStore` Protocol), ADR 0015 (sixth audit; `BL-197`-`208` plus the deferred close `BL-209`-`211` covering Namespace TTL validation, sweeper resilience, redactor recursion cap, OpenAI batch malformed-line robustness, wall-clock budget-event parity, contract-start orphan, parse-skill recursion, multi-dispatcher NaN weights, evaluate-trajectory mislabel, instrumented-dispatcher failure telemetry, routing-lane exclusion, plus the BL-196 multi-key KeyError catch, the wrap_encrypted docstring extension, and the markdown comment-tracker rewrite), ADR 0016 (`BL-133`: `SkillContractExecutor` Protocol + `InProcessSkillContractExecutor` default + `SubprocessSkillContractExecutor` opt-in for crash + rlimit isolation), ADR 0017 (seventh audit; `BL-215`-`BL-218` covering the `SkillLoader` UTF-8 decode boundary, subprocess IPC frame length bound, `SubprocessSkillContractExecutor` metadata validation, and `read_text` UTF-8 encoding consistency, bringing the IPC trust boundary introduced by ADR 0016 to the same "external-input-must-not-crash" invariant as the audit-path and bulk-decode sides), ADR 0018 (eighth audit; `BL-219`-`BL-222` covering the `JsonlSink` UTF-8 write-side encoding, subprocess child-side partial-header EOF treatment, `BudgetTracker` finite/non-negative validation on caller-fed `usd` / `wall_clock_seconds`, and `MultiDispatcher` per-member failure containment, extending the BL-218 read-side standard to the write side, generalising BL-216 truncated-header handling to the child, applying the BL-159 / BL-205 NaN-clamp class to the budget input boundary, and making the BL-207 / BL-208 telemetry-on-failure guarantee robust against ensemble-level cancellation), and ADR 0019 (ninth audit; `BL-223` `MultiSink` per-sink failure containment on the audit fan-out side, the BL-222 ensemble-side guarantee generalised to the sequential sink fan-out so a single failing sink does not block downstream sinks from receiving the event, the BL-202 / BL-167 audit-vs-raise parity invariant upheld at every fan-out leg, with `BaseException` still propagating per the BL-165 invariant), ADR 0020 (tenth audit; `BL-226` / `BL-227` S3 user-metadata trust-boundary parsing via `_safe_float` / `_safe_int` and `BoundedS3Store.evict_to_capacity` per-key DELETE containment), ADR 0021 (eleventh audit; `BL-228` `RoutingChainDispatcher` per-link failure containment and `BL-229` S3 metadata-scan HEAD not-found containment, closing the two open ADR 0020 revisit triggers, with the DynamoDB `float(exp)` shape left unchanged as a documented non-finding, `BL-230`), and ADR 0022 (twelfth audit; `BL-231` / `BL-232` non-finite numeric configuration validation, generalising the BL-159 / BL-205 / BL-221 / BL-226 NaN class from the value/data boundaries to the numeric configuration boundaries: `ActionBudget` / `RetryPolicy` reject `NaN` / `+inf` / `-inf` / negative limits at construction, the dual of BL-221, and `MCPServerSpec.timeout_seconds` / `TTLSweeper.interval_seconds` close the `NaN` hole in their `<= 0` positivity guards), and ADR 0023 (thirteenth audit; `BL-233` extends the BL-222 / BL-223 / BL-227 / BL-228 fan-out per-member failure containment class from `evict_to_capacity` to the sibling periodic-TTL-sweep path, so `S3Store._sweep_sync` and `DynamoDBStore._sweep_sync` contain a per-item network DELETE `Exception` and one transient `SlowDown` / `ProvisionedThroughputExceeded` / network blip no longer aborts the whole sweep pass, the inspection step staying fail-loud and only the idempotent DELETE best-effort, closing the question ADR 0020 / 0021 / 0022 deferred from the BL-229 `_head_metadata` scope), and ADR 0024 (`BL-234` / `BL-235`, closing `BL-135`: memory compaction, summarisation, and hot/cold tiering as drivers and compositions over the existing Protocols, the `Summarizer` Protocol + `TruncatingSummarizer` deterministic reference, `MemoryCompactor` with version-gated atomic compaction over the ADR 0014 `VersionedMemoryStore` + `TransactionalMemoryStore` surface and an explicit `atomic=False` best-effort opt-in, and `TieredMemoryStore` with CAS-guarded promotion, hot-first writes with cold invalidation, cold-first deletes, version-gated demotion, and BL-212-ranked `demote_to_capacity`; no new store Protocol, no adapter changes, LRU and model-quality summarisation stay out of tree), and ADR 0025 (fourteenth audit; the full-pass engagement protocol with evidence under `audit/`: `BL-236` stale pip-audit suppression removal, `BL-237` `uv lock --check` lockfile-freshness gate, `BL-238` unused `logfire` declaration drop with the resolved graph unchanged, `BL-239` comment-accuracy fixes; no runtime code finding, the ADR 0024 modules' first audit coverage clean).

Security conventions: untrusted skill bundles are loaded with `allow_contract=False` (no `contract.py` execution) and bounded extraction; pin an immutable `ref` plus a `sha256` for tamper-evident installs; wrap event sinks in `harness.RedactingSink` when arguments may carry secrets. An opted-in `contract.py` can run in `SubprocessSkillContractExecutor` for crash + rlimit isolation; capability isolation (container / seccomp) stays the out-of-tree extension point (see `LIMITATIONS.md` L3, ADR 0016).

## Contributing

See `CONTRIBUTING.md`. Issues and PRs welcome. Security-relevant findings: see `SECURITY.md`.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.
