# Backlog register (2026-06-12 audit pass)

This file is the deferred-items register required by the 2026-06-12
full-pass engagement protocol. The canonical line-item tracker for
this repository is [`docs/backlog.md`](./docs/backlog.md); every row
here links into it, and status transitions happen there. Findings
register: [`audit/02-security-findings.md`](./audit/02-security-findings.md).
Decisions: [ADR 0025](./docs/adr/0025-fourteenth-audit-full-pass.md),
[ADR 0026](./docs/adr/0026-prompt-caching-on-the-runtime-adapter.md).

Updated 2026-06-12 (same-day backlog session): `BL-240` and `BL-241`
were decided by the maintainer and delivered, and the re-triage found
two lifted upstream blockers, of which `BL-132` / `BL-171` (prompt
caching) was implemented (ADR 0026) and `BL-114` (non-replay resume,
`DeferredToolRequests` now stable upstream) remains open as the next
candidate wave.

Ordering: severity, then effort. Effort scale S/M/L.

## Security

No open items: `BL-240` (secret-scan CI job) was decided and
delivered on 2026-06-12, see "Resolved" below.

## Reliability

| ID | Title | Severity | Effort | Rationale and suggested approach | Dependencies | Suggested owner role |
|---|---|---|---|---|---|---|
| `BL-155` | True wall-clock preemption for non-cooperative tools | medium | L | Pre-existing, tracked: watchdog preempts only at await boundaries (`LIMITATIONS.md` L11). Needs a thread/process execution boundary. | design wave | maintainer |
| `BL-179` | RetryPolicy partial-usage accounting on failed attempts | low | M | Pre-existing, tracked: needs upstream PydanticAI partial-usage on the exception path. | upstream | maintainer |

## Quality

| ID | Title | Severity | Effort | Rationale and suggested approach | Dependencies | Suggested owner role |
|---|---|---|---|---|---|---|
| `BL-120` | Live-model reference workload (CI smoke, key-gated) | medium | L | Pre-existing, tracked; the highest-leverage open item per `STATUS.md`, and since ADR 0026 also the live cache-hit gate for the `BL-132` / `BL-171` wiring. Needs funded API keys and a credentialed CI gate; out of scope for an automated pass. | live credentials | maintainer |
| `BL-114` | Non-replay approval resume | low | L | Re-triaged 2026-06-12: the upstream primitive (`DeferredToolRequests` / `DeferredToolResults`) is now stable public API in pydantic-ai 1.106, so the blocker has lifted. Remains open as a dedicated wave: it rebuilds the approval pause/resume path and interacts with the `BL-193` argument binding and the `BL-154` budgets-across-pause contract, so it needs its own ADR and session. | design wave (unblocked) | maintainer |

## Documentation

No open items: `BL-241` (DCO reconciliation) was decided
(reword-to-match-practice) and delivered on 2026-06-12, see
"Resolved" below.

## Tooling

| ID | Title | Severity | Effort | Rationale and suggested approach | Dependencies | Suggested owner role |
|---|---|---|---|---|---|---|
| `BL-113` / `BL-138` | True OTel spans + GenAI semantic conventions | low | L | Pre-existing, tracked: blocked on the OTel logs SDK stabilising. | upstream | maintainer |

## Resolved (audit pass + same-day backlog session)

`BL-236` (stale pip-audit suppression removed), `BL-237`
(`uv lock --check` CI gate), `BL-238` (unused `logfire` declaration
dropped), `BL-239` (tiering stamp-map caveat + `_expiry` wording),
`BL-240` (blocking gitleaks `secret-scan` job + `.gitleaks.toml`
fixture allowlist), `BL-241` (DCO certification by PR submission),
`BL-132` / `BL-171` (prompt caching on the runtime adapter,
ADR 0026; live cache-hit residual carried by `BL-120`). Details in
`docs/backlog.md` ("Fourteenth audit" and the in-place L3 rows),
ADR 0025, and ADR 0026.
