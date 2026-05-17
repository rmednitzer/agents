# ADR 0012: Run provenance records and optional provider batch capabilities (Anthropic, OpenAI)

- Status: Accepted
- Date: 2026-05-17
- Authors: rmednitzer
- Builds on: ADR 0001-0011

## Context

A cross-repo review compared this repo's run-validation machinery
against the sibling `sentinel` corpus. `sentinel` runs a routine,
emits a schema-versioned artifact stamped with the content hash of the
codebook and routine that produced it, and re-validates the corpus in
CI. This repo had no equivalent: the evaluation gate (BL-130) measures
whether dispatch / trajectory quality still clears a threshold, but
nothing binds a completed run to the content of the contract that
enforced it, and there is no re-validatable record of what actually
ran.

The review explicitly did not assume `sentinel` was correct. Two real
defects in its approach were found and are deliberately *not* copied:

1. `sentinel/.github/scripts/check_provenance.py` resolves the
   producing config's hash from `git log -1` on the artifact path, so
   a later fix PR "advances the anchor". The attestation therefore
   proves only internal consistency at some commit, not the config in
   force when the classification was generated.
2. `sentinel`'s `check_consistency.py` routes window-overlap and
   INDEX-coverage findings into `warnings` and `main()` returns 0 when
   only warnings exist, so the "blocking" claim in its README is not
   actually enforced.

Separately, the user asked for optional Anthropic API capabilities a
workload could profit from (Message Batches, prompt caching) that the
single-run `Runtime` Protocol does not cover.

## Decision

### 1. Run-provenance records (BL-185)

`harness/provenance.py` adds `RunRecord`, a frozen, schema-versioned,
self-attesting record of one completed `run_under_contract` call, and
`contract_digest`, the SHA-256 of a contract's behavioural surface
(identity plus the name+severity of every pre/invariant/post/governance
predicate and the sorted `approval_required` list, order-independent).

`run_under_contract` gains one additive keyword, `record_sink`. When
provided, it is invoked exactly once at the run's terminal point
(completed / paused / each hard violation / runtime budget /
governance / approval-denied) with a `RunRecord`. Omitting it
reproduces prior behaviour exactly (ADR 0007).

Divergence from `sentinel` defect 1: the digest is taken from the live
(post-composition) `Contract` object *inside the enforcement loop*, so
it is bound to what actually ran. There is no version-control round
trip and nothing to re-stamp. `verify_run_record` and the offline gate
`scripts/check_run_records.py` re-validate a persisted corpus
(schema-version dispatch, model validation, run-id non-empty,
`completed_at` not before `started_at`, and digest match against an
optional `name@version -> digest` registry).

Divergence from `sentinel` defect 2: every finding in
`verify_run_record` and `check_run_records.py` is a hard error and the
gate exits non-zero on any. There is no warn-and-pass tier; a record
citing an unknown contract is itself an error, not a silent pass.

`RunRecord`'s JSON Schema is emitted by `scripts/gen_schema.py` to
`docs/schema/run-record.json` and guarded by the existing
`--check` drift gate, so a model change that is not regenerated fails
CI (the schema-version-dispatch lesson from `sentinel`'s
`validate_artifacts.py`, applied through this repo's existing
mechanism).

### 2. Optional Anthropic API capabilities (BL-186)

`harness/anthropic_api.py` adds two capabilities behind a new optional
`anthropic` extra, following the ADR 0007 backend convention (lazy
import behind a Protocol; the module imports and type-checks whether or
not the SDK is installed, via an `anthropic.*` mypy override). Note the
SDK is in practice already present in a default install because the
base `pydantic-ai` dependency pulls it transitively; the extra is the
explicit, slim-base-friendly declaration and the lazy import decouples
the code from the SDK's API, not a claim the SDK is otherwise absent.
Switching the base dependency to `pydantic-ai-slim` to make Anthropic
truly optional is a larger, provider-surface-wide decision deferred to
its own change:

- `AnthropicBatchProcessor` wraps the Message Batches API
  (asynchronous bulk processing at 50% token price). It takes the
  batches resource by dependency injection so its logic is fully
  testable with a fake; only `from_env` touches the real SDK and it
  raises a clear error naming the extra when absent. The wrapper is
  sync and does not poll on a timer: the caller owns the wait loop so a
  harness can interleave budget / cancellation checks.
- `cache_control_system` builds a correctly shaped, prefix-stable
  cached system block, with the silent-invalidator constraint
  documented at the call site.

`harness/openai_api.py` adds the OpenAI counterpart, `OpenAIBatchProcessor`
(`BL-187`), behind an `openai` extra with the same conventions. It is
deliberately not a copy: the OpenAI Batch API uploads a JSONL request
file, creates a batch referencing the file id, and produces a JSONL
output (and separate error) file, so the submit/results implementation
is genuinely different. There is no `cache_control` analogue (OpenAI
prompt caching is automatic), so nothing mirrors `cache_control_system`.
`OpenAIBatchRequest` has no default `model`: this code cannot verify
current OpenAI model identifiers against a trusted source, and a guessed
id would be a silent mis-identification, so the caller passes it
explicitly. Model-level OpenAI already works through the provider-neutral
`Runtime`; only the bulk surface needed a wrapper.

This is purely additive: new modules (`harness/provenance.py`,
`harness/anthropic_api.py`, `harness/openai_api.py`) and two new
optional extras (`anthropic`, `openai`). The only change to an existing
signature is a new keyword-only `record_sink` on `run_under_contract`,
which defaults to `None` and reproduces prior behaviour exactly; no L1
import path was removed or changed and the `Runtime` Protocol is
untouched (batching is a different shape and is intentionally not
forced into it).

## Consequences

- A workload can opt into a re-validatable provenance trail by passing
  `record_sink`; persisted records can be gated in CI via
  `scripts/check_run_records.py`.
- Bulk / offline workloads can use the Batches API at half cost without
  the per-call `Runtime` adapter, and prompt caching has a first-class
  helper.
- Blast radius: `enforcement.py` gained terminal-point emit calls
  guarded by `record_sink is None`; with the default the control flow
  and exceptions are byte-for-byte the prior behaviour. Rollback is
  removing the keyword and the two new modules; nothing depends on them
  unless opted in. Residual scope is in `LIMITATIONS.md`.
