# Status

Maturity of the repository and its documents. Updated when a phase
opens or closes. Last reviewed: 2026-05-24 (`BL-224` BoundedDynamoDBStore).

## Maturity taxonomy

- `stable`: implemented, tested, merged to `main`, contract unlikely to
  change without an ADR.
- `in-progress`: partly delivered; surface may move.
- `planned`: tracked in `docs/backlog.md`, not started.

## Phase tracking

| Phase | Scope | Status | Reference |
| --- | --- | --- | --- |
| L1 | Contract surface, budgets, memory namespace, workloads, skills, runtime Protocol | stable | ADR 0001-0006 |
| L2 | Guard and budget wiring, durable backends, observability, composition, skill install | stable | ADR 0007, PR #20 (`af1df9d`) |
| L3 Tier 0 | Skill-install and event security hardening, the audit fixes, REUSE + dependency-audit gate | stable (commit-SHA pinning is the tracked remainder) | ADR 0008, ADR 0009, ADR 0010 |
| L3 default-path wiring | `BL-100`-`BL-104` (composition, drift, recovery directives, default dispatcher, run lifecycles), additive | stable | ADR 0010 |
| L3 Tier 1-2 | Cost/per-tool budgets, retry policy, structured soft-reject, concrete embedding provider, entry-point + CLI extensions | stable | ADR 0010 |
| L3 Tier 3-4 | Governance (REUSE), release lifecycle and operations | stable | ADR 0010 |
| L3 capability wave | Key providers + rotation (`BL-111`), attribute-based ACL + audited denial (`BL-122`), MVCC version tokens (`BL-124`), semantic memory (`BL-131`), the evaluation gate (`BL-130`), the third-audit fixes (`BL-172`-`BL-178`), additive | stable | ADR 0011 |
| Run provenance + provider batch capabilities | `RunRecord` + `contract_digest` + offline gate (`BL-185`), `AnthropicBatchProcessor` + prompt-cache helper (`BL-186`), `OpenAIBatchProcessor` (`BL-187`), additive | stable | ADR 0012 |
| Fifth code audit | Read-vs-listing expiry boundary (`BL-188`), OpenAI batch error diagnostic (`BL-189`), `LocalSkillSource` symlink-safe clear (`BL-190`), JSON span-list memory ceiling (`BL-191`), provenance-gate registry validation (`BL-192`), additive | stable | ADR 0013 |
| Approval-resume argument binding (`BL-193`) | Stale approval for the same tool with different arguments no longer satisfies a new call on resume; the resolved-decision lookup binds by the full `(tool, arguments)` tuple, additive | stable | PR #46 (`a511760`), ADR 0013 follow-up |
| Versioned + transactional memory on durable adapters (`BL-180`) | `VersionedMemoryStore` on `RedisStore` + `DynamoDBStore` (closes the BL-124 remainder); new `TransactionalMemoryStore` Protocol + reference impls on InMemory/SQLite/Redis/DynamoDB (atomic multi-key version-gated transactions); `wrap_acl` forwards the new Protocol, additive | stable | ADR 0014 |
| Expiry-boundary predicate consolidation (`BL-195`) | `memory/_expiry.py` is the one Python-side `is_live` / `is_expired` for every adapter; the SQL / DynamoDB-DSL counterparts are documented equivalents in the helper's module docstring. Collapses the five pointwise BL-157 / BL-168 / BL-177 / BL-188 / BL-180 fixes onto one invariant. Additive; no observable behaviour change | stable | Runbook 7.4 #1 |
| EncryptedStore multi-key legacy migration (`BL-196`) | New optional `IterableKeyProvider` Protocol (`iter_key_ids`); `RotatingKeyProvider` implements it; new opt-in `legacy_multi_key: bool = False` on `EncryptedStore.__init__` and `wrap_encrypted` iterates the historical ring on the legacy fallback path. Lifts the L16 "current-key only" restriction without changing the default; AES-GCM authentication still gates every attempt | stable | Runbook 7.4 #4 |
| Sixth code audit (`BL-197`-`BL-208`) | Twelve additive findings: Namespace TTL validation + `resolve_ttl` consolidation, RedisStore.mset empty short-circuit, TTLSweeper failure resilience, Redactor recursion cap, OpenAI batch non-dict line, wall-clock boundary event parity, ContractStarted orphan, parse_skill_md recursion, MultiDispatcher NaN weights, evaluate_trajectory input mislabel, InstrumentedDispatcher failure telemetry, routing-lane dispatcher exclusion. All class extensions of prior fixes or novel diagnostic-gap findings; 45 new regression tests | stable | ADR 0015 |
| ADR 0015 deferred close (`BL-209`-`BL-211`) | The three items ADR 0015 flagged as deferred (M3 / M6 / H5): EncryptedStore BL-196 multi-key loop catches KeyError alongside InvalidTag for out-of-tree KMS providers; `wrap_encrypted` docstring extended to flag Versioned / Transactional alongside CAS as deliberately not-forwarded; `MarkdownValidatorRuntime` per-line comment tracker rewritten as a position-aware scanner. 12 new regression tests; no new ADR (folded into the ADR 0015 record) | stable | ADR 0015 deferred |
| Skill contract execution isolation (`BL-133`) | New `skills.execution.SkillContractExecutor` Protocol with two in-tree references: `InProcessSkillContractExecutor` (default, backward-compatible) and `SubprocessSkillContractExecutor` (load + evaluate in a long-lived Python subprocess with `resource.setrlimit` caps on POSIX). Crash isolation is real; resource limits are bounded; capability isolation (fs / network / syscall) stays the out-of-tree extension point. `LIMITATIONS.md` L3 rewritten | stable | ADR 0016 |
| Sweeper size bound across every in-tree adapter (`BL-212`-`BL-214`, `BL-224`, `BL-225`) | New `BoundedSweepableStore` extension Protocol + `TTLSweeper(max_keys=...)` capacity pass. `InMemoryStore` (Python dict-order FIFO with overwrite-keeps-position), `SQLiteStore` (rowid ASC with overwrite-shifts-to-newest), `BoundedRedisStore` opt-in subclass (sorted-set index scored by a per-namespace server-side INCR counter, robust against client clock skew), `BoundedDynamoDBStore` opt-in subclass (per-namespace `seq` Number attribute on every item, allocated via atomic `UpdateItem ADD seq :n` on a per-namespace counter row; scan + client-side sort + batch_write_item delete), `BoundedS3Store` opt-in subclass (per-object `insertion-order` user-metadata attribute stamped from `time.time_ns()` at write time; LIST + HEAD + client-side sort + per-key DELETE). Closes the size-bound half of `BL-135` for every in-tree adapter; only the compaction / summarisation / tiering half stays tracked | stable | PRs #58/#59/#60/#65/#78 |
| Seventh code audit (`BL-215`-`BL-218`) | Four additive findings: `SkillLoader` UTF-8 decode boundary, subprocess IPC frame length bound, `SubprocessSkillContractExecutor` metadata validation, `read_text` UTF-8 encoding consistency. Brings the IPC trust boundary introduced by ADR 0016 to the same "external-input-must-not-crash" invariant as the audit-path and bulk-decode sides; 15 new regression tests | stable | ADR 0017 |
| Eighth code audit (`BL-219`-`BL-222`) | Four additive findings: `JsonlSink` UTF-8 write-side encoding, subprocess child-side partial-header EOF treatment, `BudgetTracker` finite/non-negative validation on caller-fed `usd` / `wall_clock_seconds`, `MultiDispatcher` per-member failure containment. Brings the BL-218 read-side standard to the write side, generalises BL-216 truncated-header handling to the child, applies the BL-159 / BL-205 NaN-clamp class to the budget input boundary, and makes the BL-207 / BL-208 telemetry-on-failure guarantee robust against ensemble-level cancellation; 20 new regression tests | stable | ADR 0018 |
| Ninth code audit (`BL-223`) | One additive finding: `MultiSink` per-sink failure containment on the audit fan-out side, the BL-222 ensemble-side guarantee generalised to the sequential sink fan-out so a single failing sink (OTel exporter, JsonlSink with disk full) does not block downstream sinks from receiving the event. `BaseException` (KeyboardInterrupt / SystemExit / CancelledError) still propagates; terminal signals are not swallowed. 7 new regression tests; the audit-vs-raise parity invariant (BL-202 / BL-167) now holds at every fan-out leg | stable | ADR 0019 |
| Tenth code audit (`BL-226`-`BL-227`) | Two additive findings against the just-merged `BL-225` `BoundedS3Store`: S3 user-metadata trust-boundary parsing (`_safe_float` rejects NaN / +inf / -inf / unparseable; `_safe_int` defaults to 0 -- the legacy-migration sentinel) applied at every metadata-read site, generalising the BL-159 / BL-201 / BL-205 / BL-215 / BL-217 / BL-221 invariants to the S3 metadata boundary; `BoundedS3Store.evict_to_capacity` per-key DELETE containment, generalising the BL-222 / BL-223 fan-out failure invariant and the BL-202 / BL-167 audit-vs-raise parity to the new BL-225 sequential-DELETE path. 33 new regression tests | stable | ADR 0020 |
| L3 open | Live-model workload, memory compaction / summarisation / tiering, true OTel spans, prompt caching, true preemption, non-replay resume | planned | `docs/backlog.md` (`BL-120`, `BL-135`, `BL-113`/`138`, `BL-132`/`171`, `BL-155`, `BL-114`) |

## Document maturity

| Document | Maturity |
| --- | --- |
| `CLAUDE.md`, `README.md`, component `README.md` | stable |
| `docs/adr/0001`-`0020` | stable (Accepted) |
| `docs/releasing.md` | stable |
| `docs/backlog.md` | living tracker |
| `SECURITY.md`, `CONTRIBUTING.md`, `GOVERNANCE` section (in `CONTRIBUTING.md`) | stable |
| `STATUS.md`, `LIMITATIONS.md`, `CHANGELOG.md` | living |

## Release

Pre-1.0 (`0.0.1`, Development Status 2, Pre-Alpha). No release tags or
published package yet; `main` is the only supported branch. The
versioning and release policy and the tag-triggered release workflow
now exist (`docs/releasing.md`, `.github/workflows/release.yml`,
`BL-151`); publishing the first release to an index remains a
deliberate human gate. This file and `LIMITATIONS.md` state what is and
is not production-ready.
