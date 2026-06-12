# Backlog register (2026-06-12 audit pass)

This file is the deferred-items register required by the 2026-06-12
full-pass engagement protocol. The canonical line-item tracker for
this repository is [`docs/backlog.md`](./docs/backlog.md); every row
here links into it, and status transitions happen there. Findings
register: [`audit/02-security-findings.md`](./audit/02-security-findings.md).
Decisions: [ADR 0025](./docs/adr/0025-fourteenth-audit-full-pass.md).

Ordering: severity, then effort. Effort scale S/M/L.

## Security

| ID | Title | Severity | Effort | Rationale and suggested approach | Dependencies | Suggested owner role |
|---|---|---|---|---|---|---|
| `BL-240` (findings F-3) | Secret-scan CI job with fixtures allowlist | info | S | gitleaks over tree + history finds only the four synthetic `AKIA...` redaction fixtures; a CI job would make that check continuous. Approach: `gitleaks` action step plus a `.gitleaks.toml` allowlist scoped to `tests/harness/test_redaction.py`. Weigh against the existing compensating controls (GitHub secret scanning, CodeQL, per-audit manual scan). | none | maintainer |

## Reliability

| ID | Title | Severity | Effort | Rationale and suggested approach | Dependencies | Suggested owner role |
|---|---|---|---|---|---|---|
| `BL-155` | True wall-clock preemption for non-cooperative tools | medium | L | Pre-existing, tracked: watchdog preempts only at await boundaries (`LIMITATIONS.md` L11). Needs a thread/process execution boundary. | design wave | maintainer |
| `BL-179` | RetryPolicy partial-usage accounting on failed attempts | low | M | Pre-existing, tracked: needs upstream PydanticAI partial-usage on the exception path. | upstream | maintainer |

## Quality

| ID | Title | Severity | Effort | Rationale and suggested approach | Dependencies | Suggested owner role |
|---|---|---|---|---|---|---|
| `BL-120` | Live-model reference workload (CI smoke, key-gated) | medium | L | Pre-existing, tracked; the highest-leverage open item per `STATUS.md`. Needs funded API keys and a credentialed CI gate; out of scope for an automated pass. | live credentials | maintainer |
| `BL-132` / `BL-171` | Prompt and response caching on the runtime adapter | low | M | Pre-existing, tracked: needs a verified PydanticAI provider-cache API plus a live model to validate. | upstream + `BL-120` | maintainer |
| `BL-114` | Non-replay approval resume | low | L | Pre-existing, tracked: needs a stable upstream pause/resume primitive. | upstream | maintainer |

## Documentation

| ID | Title | Severity | Effort | Rationale and suggested approach | Dependencies | Suggested owner role |
|---|---|---|---|---|---|---|
| `BL-241` (findings D-8) | DCO sign-off: enforce or reword | info | S | CONTRIBUTING.md mandates per-commit sign-off; history carries no trailers and CI does not check. Either add a DCO check or reword to match squash-merge practice. Deliberately left to the maintainer (governance document). | maintainer decision | maintainer |

## Tooling

| ID | Title | Severity | Effort | Rationale and suggested approach | Dependencies | Suggested owner role |
|---|---|---|---|---|---|---|
| `BL-113` / `BL-138` | True OTel spans + GenAI semantic conventions | low | L | Pre-existing, tracked: blocked on the OTel logs SDK stabilising. | upstream | maintainer |

## Resolved by this pass (for cross-reference)

`BL-236` (stale pip-audit suppression removed), `BL-237`
(`uv lock --check` CI gate), `BL-238` (unused `logfire` declaration
dropped), `BL-239` (tiering stamp-map caveat + `_expiry` wording).
Details in `docs/backlog.md` ("Fourteenth audit") and ADR 0025.
