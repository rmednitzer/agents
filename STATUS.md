# Status

Maturity of the repository and its documents. Updated when a phase
opens or closes. Last reviewed: 2026-05-17.

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
| L3 open | Live-model workload, memory compaction/tiering, true OTel spans, prompt caching, true preemption, non-replay resume, multi-key transactions | planned | `docs/backlog.md` (`BL-120`, `BL-135`, `BL-113`/`138`, `BL-132`/`171`, `BL-155`, `BL-114`, `BL-180`) |

## Document maturity

| Document | Maturity |
| --- | --- |
| `CLAUDE.md`, `README.md`, component `README.md` | stable |
| `docs/adr/0001`-`0011` | stable (Accepted) |
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
