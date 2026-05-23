# Status

Maturity of the repository and its documents. Updated when a phase
opens or closes. Last reviewed: 2026-05-23.

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
| L3 open | Live-model workload, memory compaction/tiering, true OTel spans, prompt caching, true preemption, non-replay resume | planned | `docs/backlog.md` (`BL-120`, `BL-135`, `BL-113`/`138`, `BL-132`/`171`, `BL-155`, `BL-114`) |

## Document maturity

| Document | Maturity |
| --- | --- |
| `CLAUDE.md`, `README.md`, component `README.md` | stable |
| `docs/adr/0001`-`0014` | stable (Accepted) |
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
