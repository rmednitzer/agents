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

Submitting a pull request certifies the Developer Certificate of
Origin 1.1 for its contents: you have the right to submit the work
under the project license. A per-commit `Signed-off-by` trailer
(`git commit -s`) is welcome but not required: pull requests are
squash-merged, which consolidates per-commit trailers away, so the
pull request itself is the certification record (`BL-241`,
ADR 0025).

## Licensing

Contributions are licensed under Apache-2.0 (see [LICENSE](./LICENSE)
and [NOTICE](./NOTICE)). The repository is REUSE 3.x compliant: a
top-level `REUSE.toml` declares copyright and license for the whole
tree (and `LICENSES/Apache-2.0.txt` holds the license text), so a new
file is covered automatically and needs no per-file header. CI gates
this with `reuse lint` (`BL-152`).

<!-- REUSE-IgnoreStart -->
A per-file `SPDX-License-Identifier: Apache-2.0` header is still
welcome where the file type supports comments (the `REUSE.toml`
`precedence = "aggregate"` lets an inline header coexist with the
tree-wide default), but it is no longer required.
<!-- REUSE-IgnoreEnd -->

DCO certification is by pull-request submission; see "Commit
messages and sign-off".

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
