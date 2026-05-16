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
- Issues in upstream dependencies (report upstream first; reference here once a fix lands).
- Findings requiring physical access to a host running the harness.

## Supported versions

Pre-1.0 software. Only the `main` branch is supported.
