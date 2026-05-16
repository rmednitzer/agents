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
- Harness contract violations (sandbox escape, action budget bypass, tool-use authorization bypass).
- Memory isolation failures (cross-namespace read or write, lineage tampering).
- Skill loading vulnerabilities (path traversal, code execution via crafted SKILL.md or bundled assets).

Out of scope:
- Issues in upstream dependencies (report upstream first; reference here once a fix lands).
- Findings requiring physical access to a host running the harness.

## Supported versions

Pre-1.0 software. Only the `main` branch is supported.
