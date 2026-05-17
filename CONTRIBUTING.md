# Contributing

## Before opening a PR

1. Read [CLAUDE.md](./CLAUDE.md) for repository conventions and
   [STATUS.md](./STATUS.md) for what is stable versus in progress.
2. Open an issue for non-trivial changes; discuss the contract before
   coding.
3. Run `make check` (ruff, mypy strict, pytest) locally. CI also
   enforces 94% coverage and `gen_schema.py --check`.
4. Changes are additive to the L1 Protocols (ADR 0007). A contract
   change needs a new ADR under `docs/adr/` and a line in
   [CHANGELOG.md](./CHANGELOG.md).

## Commit messages and sign-off

Conventional Commits style (`feat:`, `fix:`, `docs:`, `refactor:`,
`test:`, `chore:`, `build:`, `ci:`).

Sign off every commit (Developer Certificate of Origin 1.1):
`git commit -s`. The trailer `Signed-off-by: Name <email>` certifies
you may submit the work under the project license.

## Licensing

Contributions are licensed under Apache-2.0 (see [LICENSE](./LICENSE)
and [NOTICE](./NOTICE)). New files should carry an
`SPDX-License-Identifier: Apache-2.0` header where the file type
supports comments. A full REUSE conversion is tracked as `BL-152`.

## PR description

State:

- What changed.
- Blast radius (which components, which contracts).
- Rollback path.
- Security review: for changes to the harness or memory contracts,
  skill or workload loading, or event content, state the threat
  considered and the residual risk. See [SECURITY.md](./SECURITY.md).

## Governance

Single maintainer (`@rmednitzer`, see `.github/CODEOWNERS`) with final
authority over merges, `CLAUDE.md`, ADRs, and CI. Decision artifacts:
ADRs (technical decisions), `docs/backlog.md` (what ships and its
state), `CHANGELOG.md` (material changes). Security disclosures follow
the response targets in `SECURITY.md`.
