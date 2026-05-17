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
- Skill loading vulnerabilities (path traversal via skill name or archive member, code execution via crafted SKILL.md or bundled assets) and out-of-tree workload loading.

Out of scope:
- Issues in upstream dependencies (report upstream first; reference here once a fix lands). Dependencies are lockfile-pinned (`uv.lock`); Dependabot proposes `pip` and `github-actions` updates.
- Findings requiring physical access to a host running the harness.

## Hardening posture

- Skill install (`skills.sources.GitHubSkillSource`): archive download, member count, per-member size, and total uncompressed size are bounded; an optional `sha256` verifies the tarball. A branch `ref` is mutable; pin an immutable ref (commit SHA or release tag) plus a checksum for tamper-evident installs.
- Skill contracts: `install_skill` does not execute a bundled `contract.py` by default (`allow_contract=False`). This gate is defence in depth, not a sandbox; an opted-in contract still runs arbitrary Python. See [LIMITATIONS.md](./LIMITATIONS.md) L3 and ADR 0008.
- Event content: wrap a sink in `harness.RedactingSink` to scrub secrets and PII before events reach a sink.
- Static analysis: CodeQL runs on push, pull request, and weekly.

## Supported versions

Pre-1.0 software. Only the `main` branch is supported. Scope and
residual risk are tracked in [LIMITATIONS.md](./LIMITATIONS.md).
