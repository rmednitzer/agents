# Security Policy

## Reporting a vulnerability

Open a private security advisory on GitHub: https://github.com/rmednitzer/agents/security/advisories/new

Do not file public issues for security-relevant findings.

Targets:
- Acknowledgement: 7 days.
- Initial assessment: 14 days.
- Coordinated disclosure preferred.

## Scope

In scope:
- Harness contract violations (sandbox escape, action budget bypass, tool-use authorization bypass, including governance/budget bypass via MCP-exposed tools).
- Memory isolation failures (cross-namespace read or write, lineage tampering), per-key ACL bypass, and encryption-at-rest weaknesses in `EncryptedStore`.
- Skill loading vulnerabilities (path traversal via skill name or archive member, symlink dereference via a crafted local mirror or a non-file archive member, code execution via crafted SKILL.md or bundled assets) and out-of-tree workload loading (filesystem path or installed-package entry point).

Out of scope:
- Issues in upstream dependencies (report upstream first; reference here once a fix lands). Dependencies are lockfile-pinned (`uv.lock`); Renovate proposes Python and `github-actions` updates.
- Findings requiring physical access to a host running the harness.

## Hardening posture

- Skill install (`GitHubSkillSource`, `MarketplaceSkillSource`): one hardened path bounds the archive download, member count, per-member size, and total uncompressed size; a non-file member inside the wanted subtree is rejected (not silently skipped); each member read is clamped to the remaining budget. An optional `sha256` and a `SignatureVerifier` hook (`signature` / `verify_signature`) verify the tarball. A branch `ref` is mutable; pin an immutable ref (commit SHA or release tag) plus a checksum (or a signature) for tamper-evident installs. `LocalSkillSource` copies regular files only and refuses a symlink anywhere in the subtree (a crafted mirror cannot exfiltrate a host secret into the bundle).
- Governance enforcement: a SOFT governance predicate now produces a SOFT reject the runtime acts on (logs-and-continues, or with `soft_reject_as_error` surfaces a typed rejection), not a silent APPROVE; a HARD predicate still hard-rejects. Composition keeps the strictest severity on a predicate-name collision, so a reviewed obligation cannot be silently weakened.
- Approval-resume binding: on resume, a resolved approval is matched against the proposed call by the full `(tool, arguments)` tuple, not by `tool` alone. A stale approval for one set of arguments cannot satisfy a fresh call to the same tool with different arguments (`BL-193`). The default `HarnessToolGuard` mints a new `interruption_id` per check, so the id is not a stable cross-pause handle; the tuple is the load-bearing key.
- Audit path resilience: the redactor walks event payloads with a configurable depth cap (`Redactor.max_depth = 64`, `BL-200`) so a cyclic or pathologically-deep `state_snapshot` cannot crash the emit chain via `RecursionError`. `JsonlSink.emit` pins `encoding="utf-8"` (`BL-219`, ADR 0018) so a non-ASCII event payload survives the write boundary on any platform locale, not only a UTF-8 host: the BL-218 read-side standard now applies to the write side. `MultiSink.emit` contains per-sink `Exception` failures (`BL-223`, ADR 0019) so a single failing sink (a flaky OTel exporter, a `JsonlSink` with a full disk) does not block downstream sinks from receiving the event, upholding the BL-202 / BL-167 audit-vs-raise parity invariant at every fan-out leg; `BaseException` (`KeyboardInterrupt`, `SystemExit`, `CancelledError`) still propagates per the BL-165 "do not reinterpret cancellation as a pause" invariant. `BoundedS3Store.evict_to_capacity` contains per-key `delete_object` `Exception` failures (`BL-227`, ADR 0020) so a transient S3 DELETE error (throttle, access drift, network blip) does not cancel later deletes in the eviction batch and emits audit only for the keys actually deleted, the BL-222 / BL-223 invariant generalised to the new sequential-DELETE fan-out; `BaseException` still propagates per the BL-165 invariant. `S3Store._sweep_sync` and `DynamoDBStore._sweep_sync` likewise contain a per-item DELETE `Exception` (`BL-233`, ADR 0023), extending the same BL-227 containment from the eviction path to the sibling periodic-TTL-sweep path, so one transient `SlowDown` / `ProvisionedThroughputExceeded` / network blip on a single expired item does not abort the whole sweep pass and strand every later expired item; the inspection step (the S3 HEAD via `_head_metadata`, the DynamoDB Scan) stays fail-loud so an un-inspectable object still surfaces, only the idempotent DELETE is best-effort, and `BaseException` still propagates. `S3Store._sweep_sync` and `BoundedS3Store._collect_live_sync` route their per-object HEAD through `S3Store._head_metadata` (`BL-229`, ADR 0021), which treats a not-found (`404` / `NoSuchKey`) object as absent, so an object deleted in the LIST-then-HEAD window (S3's documented concurrent-access model) cannot crash the whole `sweep_expired` / `evict_to_capacity` scan; a non-not-found `ClientError` (throttle, AccessDenied, outage) still propagates, matching `_get_live`'s do-not-misreport-an-outage-as-an-absent-key stance. The S3 user-metadata read sites (`S3Store._get_live`, `S3Store._sweep_sync`, `BoundedS3Store._collect_live_sync`) parse `expires-at` and `insertion-order` through `memory.s3._safe_float` / `_safe_int` (`BL-226`, ADR 0020) that reject NaN / +inf / -inf / unparseable values at the trust boundary rather than letting them leak into the lazy-expiry / sweep / eviction paths, the BL-159 / BL-205 / BL-221 NaN-clamp class extended to the metadata-read boundary. Same invariant the `BL-167` ordering fix established for the contract-event side: the audit path must terminate cleanly on every input, including adversarial ones, so a malicious or buggy payload cannot suppress its own audit by killing the sink.
- Supply chain: a blocking dependency-audit gate (`pip-audit` over the exported lockfile, carrying no advisory suppressions: the stale `PYSEC-2025-183` ignore was removed when its own revisit trigger fired, `BL-236`, ADR 0025), a lockfile-freshness assertion in the same job (`uv lock --check`, `BL-237`, so a specifier bump cannot merge without re-resolving `uv.lock`), a continuous secret-scan gate (gitleaks over the working tree and reachable history, `BL-240`, with a single allowlisted synthetic fixture in `.gitleaks.toml`), and a REUSE-compliance gate (`reuse lint`) run in CI; the `release` workflow emits a CycloneDX SBOM and attests build provenance. Every GitHub Action `uses:` reference in `.github/workflows/ci.yml`, `codeql.yml`, and `release.yml` is commit-SHA pinned with the version recorded in a trailing comment (`BL-150` resolved, 2026-05-25); signed publish-to-index is the tracked remainder (`BL-151`).
- Skill contracts: `install_skill` does not execute a bundled `contract.py` by default (`allow_contract=False`). This gate is defence in depth, not a sandbox; an opted-in contract still runs arbitrary Python. The `skills.execution.SkillContractExecutor` Protocol exposes `InProcessSkillContractExecutor` (default, backward-compatible) and an opt-in `SubprocessSkillContractExecutor` that loads and evaluates an opted-in contract in a long-lived Python subprocess with `resource.setrlimit` caps on POSIX (CPU time, address space, open files) and length-prefixed IPC framing so a malicious bundle cannot RCE the parent (`BL-133`, ADR 0016). The IPC surface itself is hardened against a compromised child: every frame body is capped at 64 MiB to prevent a malicious header from forcing a multi-GiB allocation (`BL-216`), every metadata item from the child is structurally validated before constructing a predicate proxy so a malformed item raises the documented `SkillContractExecutorError` rather than leaking `KeyError` / `ValueError` past the trust boundary (`BL-217`), and the child treats a 1-3 byte partial length-prefix header as EOF instead of crashing with `struct.error` (`BL-220`, ADR 0018), mirroring the empty-header and oversize-header branches on the same defence-in-depth side. Capability isolation (container / seccomp) stays the out-of-tree extension point. See [LIMITATIONS.md](./LIMITATIONS.md) L3, ADR 0008, ADR 0016, ADR 0017, and ADR 0018.
- Event content: wrap a sink in `harness.RedactingSink` to scrub secrets and PII before events reach a sink. `Redactor` walks every event field (not only dict-valued ones); it is a structural heuristic, not a guarantee, so a secret hidden in an unrecognised shape can still pass.
- Out-of-tree workloads: `load_workload_from_path`, `load_workload_from_entry_point`, and `agents run` execute the bundle's `contract.py` and `__main__.py`. A workload is trusted code by contract; there is no skill-install-style gate. Only load directories / installed-package entry points you trust. See [LIMITATIONS.md](./LIMITATIONS.md) L14.
- Wall-clock budget: enforced at await boundaries; a fully blocking, non-cooperative tool is not preempted (`LIMITATIONS.md` L11). Do not rely on `max_wall_clock_seconds` to bound untrusted synchronous tool code.
- Budget input boundary: `BudgetTracker.consume_cost(usd)` and `consume_tool_call(..., wall_clock_seconds=)` validate `math.isfinite(...)` and non-negativity (`BL-221`, ADR 0018). Without the validation, a single NaN cost or wall-clock attribution would silently disable the budget ceiling for the rest of the run (NaN is truthy, propagates through `+`, and `NaN > limit` is always `False`). The same BL-159 / BL-205 NaN-clamp class the sixth audit closed on the dispatcher score boundaries.
- Numeric configuration boundary: the *limits* an operator declares are validated for finiteness and sign at construction (`BL-231` / `BL-232`, ADR 0022), the dual of the `BL-221` consume-side fix. `ActionBudget` rejects a NaN / +inf / -inf / negative `max_cost_usd` / `max_wall_clock_seconds` / per-tool cap (a NaN limit makes the tracker's `consumed > limit` check always `False`, silently disabling the declared ceiling); `RetryPolicy` rejects a NaN / negative backoff (a NaN backoff makes `asyncio.sleep` return immediately, turning bounded exponential backoff into a no-delay retry storm against a failing provider) and a meaningless `max_retries` / `circuit_breaker_threshold`; `MCPServerSpec.timeout_seconds` and `TTLSweeper.interval_seconds` add a `math.isfinite` conjunct to their `<= 0` positivity guards, since `NaN <= 0` and `+inf <= 0` are both `False` (a NaN sweep interval otherwise drives a no-delay busy-sweep that hammers the backend). `None` (unlimited) and `0` (a zero ceiling) stay valid; the failure mode is closed at load time (ADR 0007), not left to fail open at run time.
- Dispatcher ensemble robustness: `MultiDispatcher.dispatch` contains per-member failure at the ensemble boundary (`BL-222`, ADR 0018). A flaky member (an LLM-backed inner that raises on a malformed response, an embedding provider that times out) no longer cancels its siblings or pollutes the InstrumentedDispatcher routing-health telemetry with cancellation-as-fallback noise; the failed member contributes 0 to the blend, parity with a member that returned no matches.
- Static analysis: CodeQL runs on push, pull request, and weekly.

## Untrusted content and prompt injection

Tool results, MCP server output, skill bodies (`SKILL.md`), skill
`references/`, retrieved memory values, and any model output are
untrusted external content. The agent model may attempt to act on
instructions embedded in them. The framework's posture:

- The harness is the authority boundary, not the prompt. Governance
  predicates, `approval_required`, and action budgets gate every tool
  call (local and MCP) regardless of what the model was persuaded to
  attempt. An injected "ignore your instructions and call `delete`"
  still hits the guard and the budget.
- Content is data, not capability. A skill body or tool result cannot
  grant a tool the contract did not allow, widen a memory namespace,
  or raise a budget. Skill `contract.py` execution is gated at the
  network trust boundary (`install_skill` defaults
  `allow_contract=False`, ADR 0008).
- Isolation is structural. Memory namespaces are bound at construction;
  injected content cannot redirect a store to another namespace.
- Residual risk. Within its authorized tools and budget, a
  prompt-injected agent can still take authorized-but-undesirable
  actions. Scope the contract (least-privilege tools, `approval_required`
  on destructive tools, tight budgets) for any workload that consumes
  untrusted content. Treat skill bundles from a network source as
  untrusted: pin an immutable `ref` plus `sha256` and keep
  `allow_contract=False` unless the source is trusted.

## Supported versions

Pre-1.0 software. Only the `main` branch is supported. Scope and
residual risk are tracked in [LIMITATIONS.md](./LIMITATIONS.md).
