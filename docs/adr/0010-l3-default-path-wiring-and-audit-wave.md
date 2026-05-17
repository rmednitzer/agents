# ADR 0010: L3 default-path wiring, audit follow-ups, governance maturity

- Status: Accepted
- Date: 2026-05-17
- Authors: rmednitzer
- Builds on: ADR 0001-0009

## Context

ADR 0008 entered L3 with security hardening and a tiered roadmap; ADR
0009 ran a full audit, fixed the clear bugs additively, and tracked the
rest as `BL-154`-`BL-162`. This ADR records the next increment: the
"highest leverage" cluster the backlog itself names (default-path
wiring, `BL-100`-`BL-104`), the well-scoped ADR 0009 follow-ups
(`BL-154`, `BL-156`, `BL-157`, `BL-161`), the well-scoped Tier 1/2
items (`BL-110`, `BL-123`, `BL-136`, `BL-137`), the out-of-tree and
CLI extensions (`BL-121`, `BL-125`), governance and release maturity
(`BL-150`-`BL-152`), and a fresh in-depth audit that surfaced bugs the
ADR 0009 pass did not. Per-item state is in `docs/backlog.md`; residual
risk is in `LIMITATIONS.md`; this ADR is the why.

## Decision

### 1. Wire the L2 primitives into the default path, additively

`BL-100`-`BL-104` move L2 primitives from opt-in to engaged through new
keyword-only parameters on `run_under_contract` whose defaults
reproduce the exact L1/L2 behaviour (ADR 0007 section 1): skill-contract
composition (`skill_contracts`), drift recording plus a
`DriftThresholdCrossed` event (`drift_monitor` / `drift_threshold`),
recovery directives (`RecoveryOutcome.directive`: continue / retry /
substitute / escalate, honoured on the postcondition stage), and
run-scoped lifecycles (`lifecycles`, a dependency-free async-context
surface so the harness does not import `memory`). No L1 import path or
signature was removed.

### 2. The ADR 0009 follow-ups that are clearly scoped, now

`BL-154` threads the consumed budget totals onto `ResumableState` and
seeds the resumed run's `BudgetTracker` from them, so budgets accumulate
across an approval pause; the non-replay resume (`BL-114`) and
already-completed-action suppression remain open, so this is "budgets
no longer reset", not "replay eliminated" (`LIMITATIONS.md` L10).
`BL-156` forwards the extension Protocols through `EncryptedStore` /
`ACLStore` by *conditional* composition (`wrap_acl` / `wrap_encrypted`):
unconditionally adding the methods would make `isinstance` lie for a
core-only backend, the exact "don't fake it" violation ADR 0004
forbids; CAS is deliberately not forwarded through encryption because
GCM nonce randomisation makes ciphertext-equality CAS unrepresentable
(a documented deviation, not a fake). `BL-157` stores DynamoDB `exp`
as float seconds and uses a float `:now` in the CAS conditions so
sub-second TTL holds and read vs compare-and-set agree at a boundary.
`BL-161` lands the deferred hardening (atomic SQLite batches, server-
side S3 prefix, looped DynamoDB/S3 scan paging, non-file archive
member rejection, per-member read clamp, `allow_contract` passthrough,
`name@version` via `rpartition`, the CLI honouring the manifest
dispatcher and a clean import-error path).

### 3. A second audit; fix the clear bugs additively

A fresh deep read (the bugs the ADR 0009 pass and the gates did not
catch) found and fixed, with regression tests:

- `HarnessToolGuard` returned APPROVE for a SOFT governance failure, so
  the soft predicate was a silent no-op and the runtime's documented
  soft-reject path was dead. It now returns REJECT/SOFT; the runtime
  logs-and-continues and (with `BL-137`) can surface a typed rejection.
- A raising `RecoveryHandler` aborted the run, contradicting "a soft
  violation never halts": the handler is now contained
  (`RecoveryApplied(recovered=False)`, continue).
- `PydanticAIRuntime.run` re-raises `CancelledError` / `BudgetExceeded`
  before consulting guard state, so a wall-clock timeout during a
  paused tool is not swallowed into a `ResumableState`.
- `compose_contracts` kept first-occurrence on a name collision, which
  could keep a SOFT predicate over a HARD one (silently weakening a
  reviewed obligation); it now keeps the strictest.
- `MemoryAudit` rejected a *missing* base key but not a *reserved* one
  (`namespace`, ...), which would raise mid-run on the first emit; it
  now rejects reserved keys at construction too.
- `SQLiteStore.sweep_expired` used `<=` while read/list use strict
  `>`, so an entry exactly at expiry was live to readers but swept;
  the boundary is now consistent.
- `LocalSkillSource` used `shutil.copytree` (dereferences symlinks), so
  a crafted local mirror could copy a host secret's contents into a
  bundle; it now copies regular files only and refuses a symlink.

### 4. Concrete provider, cost budgets, retry, structured reject

`BL-110` ships `HashingEmbeddingProvider`, a deterministic
dependency-free `EmbeddingProvider`, so `EmbeddingDispatcher` and the
new `default_dispatcher` (`BL-103`) work with no vendor and no network;
a model-quality provider stays out-of-tree by the same no-vendor-
binding stance as ADR 0001. `BL-123` adds a cost dimension and per-tool
token/wall-clock caps to `ActionBudget`/`BudgetTracker`. `BL-136` adds
an opt-in `RetryPolicy` (bounded backoff plus a per-instance circuit
breaker) that never retries a contract-terminal outcome. `BL-137` adds
`soft_reject_as_error`: a soft governance reject becomes a framework
tool-retry error the model handles as an error, not apparent tool
output. `BL-112` factors one hardened download/extract path, adds a
signature-verification hook, and a generic `MarketplaceSkillSource`.

### 5. Governance and release maturity

`BL-152` makes the repo REUSE 3.x compliant via a single `REUSE.toml`
plus `LICENSES/Apache-2.0.txt`, gated by `reuse lint` in CI. A
tree-wide `REUSE.toml` was chosen over a per-file SPDX header on ~120
files: it is spec-sanctioned, avoids massive diff churn, and does not
re-break every line-number doc citation. `BL-150` delivers the
*blocking* dependency-audit gate (`pip-audit` over the exported
lockfile, wired into `ci-success`); commit-SHA pinning of GitHub
Actions is the explicit remainder, deferred rather than faked because
the environment cannot resolve third-party action SHAs and a fabricated
40-char hash is worse than an honest tag pin (tracked like the
`BL-162` settings change). `BL-151` adds the versioning/release policy
(`docs/releasing.md`) and a tag-triggered `release` workflow (build,
CycloneDX SBOM, provenance attestation), publishing-to-index left as a
deliberate human gate pre-1.0.

### 6. ADRs are immutable; errata forward

ADR 0009 section 3 said the corrected dispatcher count is "eight"; the
actual shape is seven *router* dispatchers plus the
`InstrumentedDispatcher` *wrapper* (which the same docstring calls "a
wrapper, not a router"), so "eight" was internally contradictory. The
live docs now say "seven routers plus an InstrumentedDispatcher
wrapper" (and now a `default_dispatcher` factory). Per
`docs/adr/README.md`, ADR 0009 is not edited; this is the forward
erratum, matched in README, the `skills` docstrings, and the component
READMEs.

### 7. Honest deferrals

`BL-132` (prompt caching) needs a verified PydanticAI provider-cache
API and a live model to be meaningful (like `BL-120`); a no-op flag
would violate the "no half-finished implementation" bar, so it stays
tracked, not shipped. `BL-113`/`BL-138` (true OTel spans), `BL-131`
(semantic memory), `BL-135` (compaction), `BL-155` (true preemption),
`BL-114` (non-replay resume), `BL-130` (evaluation harness) remain
tracked: each is an L-sized surface or needs an unstable upstream, and
is better done deliberately than rushed into this wave.

## Consequences

Positive: the framework now uses its L2 primitives by default; the
exploitable and silently-wrong paths from the second audit are closed
without an L1 break; cost/retry/structured-reject close real practice
gaps; the repo is REUSE-compliant and has a dependency-audit gate and a
release lifecycle.

Negative: `run_under_contract` has more optional parameters (mitigated:
all default to the prior behaviour, one ADR documents them). A
`RetryPolicy` retrying a non-idempotent tool can repeat side effects
(documented; retries share the budget so they cannot evade it).
`HashingEmbeddingProvider` captures lexical overlap, not meaning
(documented; it is a baseline, not a model). The branch-protection and
action-SHA-pin items still need a maintainer/settings action.

Neutral: one ADR for the increment, as in ADR 0007-0009. Per-item
rationale stays in `docs/backlog.md` and docstrings.

## Revisit triggers

- A workload needs a hard cumulative budget across approval pauses with
  no tool re-execution: promote `BL-114` (non-replay resume) before it
  ships; `BL-154` alone bounds the total but the replay still repeats
  non-approval tool calls.
- A verified PydanticAI prompt-cache API stabilises: land `BL-132`.
- An L1 Protocol must change to land an item: write a dedicated ADR,
  do not fold it here.
