# Limitations

Explicit scope boundaries and known gaps. Each limit states the current
state, the implication, and the tracking item. This is pre-1.0
infrastructure; the list is expected to shrink as L3 lands. Last
reviewed: 2026-05-17.

## L1. Pre-1.0, no release lifecycle

State: version `0.0.1`, Pre-Alpha, only `main` supported, no tags or
published package. Implication: pin to a commit; expect surface change
within the additive-to-L1 rule. Tracking: `BL-151`, `STATUS.md`.

## L2. No live-model reference workload

State: the runtime adapter is tested with deterministic `TestModel` and
`FunctionModel`; only the in-process `_example` workload ships.
Implication: provider wiring, real tool-call behaviour, rate limits, and
provider failure modes are not yet exercised end to end. Tracking:
`BL-120`.

## L3. Skill execution is gated, not sandboxed

State: archive extraction is bounded and `contract.py` execution is
refused by default for installed skills (ADR 0008). A skill loaded with
`allow_contract=True` still runs arbitrary Python. Implication: only
enable contract execution for a source you trust (immutable ref plus
checksum). Tracking: `BL-133` (true isolation).

## L4. Supply-chain attestation incomplete

State: dependencies are lockfile-pinned (`uv.lock`); Dependabot covers
`pip` and `github-actions`; a CodeQL gate is added in ADR 0008. No
SBOM, no build provenance, no signed release, GitHub Actions are
tag-pinned not commit-SHA-pinned, no blocking dependency-audit gate.
Implication: not SLSA Build L2. Tracking: `BL-150`, `BL-152`.

## L5. No semantic memory or context compaction

State: `MemoryStore` is scalar key-value; there is no vector retrieval
and no summarisation or tiering. Implication: retrieval-augmented and
long-horizon workloads need an external store. Tracking: `BL-131`,
`BL-135`.

## L6. No evaluation harness

State: CI gates lint, types, and coverage, not agent behaviour.
Dispatcher routing quality and contract-outcome quality are not
measured. Implication: behavioural regressions are invisible. Tracking:
`BL-130`.

## L7. Observability is log records, not spans

State: `OTelSink` emits log records carrying trace and span ids as
attributes; there is no span tree and no GenAI semantic conventions.
Implication: no flame graphs or trace-based aggregation out of the box.
Tracking: `BL-113`, `BL-138`.

## L8. PydanticAI coupling

State: the default adapter targets a pre-1.0 library, floor-pinned and
lockfile-resolved, isolated behind the `Runtime` Protocol. Implication:
a breaking upstream change may require an adapter update. Tracking:
ADR 0001 and ADR 0003 revisit triggers.

## L9. No prompt caching or cost accounting

State: the adapter exposes no prompt-cache control and `ActionBudget`
has no cost dimension. Implication: repeated stable prefixes pay full
token cost and per-run spend is not bounded. Tracking: `BL-132`,
`BL-123`.
