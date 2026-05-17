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
| L3 Tier 0 | Skill-install and event security hardening, CI hardening | in-progress | ADR 0008 |
| L3 Tier 1-4 | AI-quality, reliability, governance, release and operations | planned | `docs/backlog.md` |

## Document maturity

| Document | Maturity |
| --- | --- |
| `CLAUDE.md`, `README.md`, component `README.md` | stable |
| `docs/adr/0001`-`0008` | stable (Accepted) |
| `docs/backlog.md` | living tracker |
| `SECURITY.md`, `CONTRIBUTING.md`, `GOVERNANCE` section (in `CONTRIBUTING.md`) | stable |
| `STATUS.md`, `LIMITATIONS.md`, `CHANGELOG.md` | living |

## Release

Pre-1.0 (`0.0.1`, Development Status 2, Pre-Alpha). No release tags or
published package yet; `main` is the only supported branch. A versioning
and release policy is tracked as `BL-151`. This file and
`LIMITATIONS.md` state what is and is not production-ready.
