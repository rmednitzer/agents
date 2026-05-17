# Changelog

Material changes by phase. Format follows Keep a Changelog; dates are
ISO 8601. Pre-1.0, so this is phase-based, not semver-tagged.

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
