# Releasing and operations

Versioning, release lifecycle, and operational notes (BL-151). This is
the policy; `.github/workflows/release.yml` is the mechanism.

## Versioning policy

- Pre-1.0 (current): version is `0.0.x`. The surface may change within
  the additive-to-L1 rule (ADR 0007); a change that is not additive to
  the L1 Protocols requires a new ADR and a `CHANGELOG.md` entry. Only
  `main` is supported (`STATUS.md`).
- From `0.1.0`: semantic versioning. MAJOR for an incompatible Protocol
  change (always with an ADR), MINOR for additive capability, PATCH for
  fixes. The L1 Protocols are the compatibility surface; an additive
  L2/L3 change is MINOR.
- The version lives in `pyproject.toml` (`project.version`). A release
  is the commit on `main` that bumps it, tagged `vMAJOR.MINOR.PATCH`.

## Release process

1. Land all changes on `main` green (CI: lint, type-check, test at 94%
   coverage, `gen_schema --check`, `reuse lint`, dependency-audit).
2. Bump `project.version` in `pyproject.toml`; move the `CHANGELOG.md`
   `[Unreleased]` section under the new version with the ISO date;
   review `STATUS.md` and `LIMITATIONS.md`.
3. Tag `vX.Y.Z` on that commit and push the tag. The `release`
   workflow runs the full quality gate again, builds the sdist and
   wheel with `uv build`, emits a CycloneDX SBOM, attests build
   provenance (SLSA-style, via GitHub artifact attestation), and
   creates a GitHub Release with the artifacts and SBOM attached.
4. Publishing to a package index is a deliberate human step (not
   automated pre-1.0): the first published release is a governance
   decision by the maintainer (`CONTRIBUTING.md` Governance).

Rollback: a release is a tag, not a mutation. To withdraw, delete the
tag/release and ship a forward fix as the next PATCH; never force-push
`main` or re-tag an existing version.

## Operations

The framework is a library plus a CLI, not a long-running service, so
"deploy" is "pin and install":

- Deploy: install from a pinned commit or release tag (pre-1.0: pin a
  commit; the lockfile `uv.lock` pins the dependency closure). Install
  only the extras a workload needs (`agents[redis]`, `agents[aws]`,
  `agents[crypto]`, `agents[otel]`).
- Rollback: re-pin to the previous commit/tag. State lives in the
  memory backend, not the package, so a code rollback is stateless.
- Memory backup and restore is per backend; the namespace is the unit:
  - `SQLiteStore`: back up the database file (WAL-checkpoint first);
    restore by file copy. One file, per-namespace tables.
  - `RedisStore`: use the server's RDB/AOF; namespace keys are prefixed
    `"<namespace>::"`, so a namespace can be dumped/restored by that
    key pattern.
  - `S3Store` / `DynamoDBStore`: use the provider's native backup (S3
    versioning or replication; DynamoDB PITR / on-demand backup). Keys
    are prefixed/partitioned by namespace.
  - `EncryptedStore`: back up the ciphertext as above; back up the
    `KeyProvider` material separately and out of band. A restore
    without the key is unrecoverable by design.
- Observability: wire an `OTelSink` (`agents[otel]`) and wrap it in a
  `RedactingSink` so events reach a collector with secrets scrubbed.

## Tracking

Signed-artifact publishing to an index, full SLSA Build L2+ provenance,
and GitHub Actions commit-SHA pinning are the open remainder of the
supply-chain hardening: `BL-150`, `BL-151`, `LIMITATIONS.md` L1/L4.
