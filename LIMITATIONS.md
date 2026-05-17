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

## L10. Budgets do not accumulate across an approval pause and resume

State: `run_under_contract` constructs a fresh `BudgetTracker` on every
call, including a `resume`, and the PydanticAI resume is a replay
(`BL-114`). Tokens, steps, tool calls, and per-tool quotas restart at
zero after each human-in-the-loop pause, and non-approval tool calls
re-execute on the replayed run. `ActionRecord` / `completed_actions`
are reserved scaffolding for a non-replay resume and are not yet
populated. Implication: a workload that pauses for approval can exceed
its declared budget by a factor of (pauses + 1), and re-executed tool
calls must be idempotent. Tracking: `BL-154`, `BL-114`.

## L11. Wall-clock watchdog preempts only at an await boundary

State: the `asyncio.wait_for` watchdog enforces `max_wall_clock_seconds`
without explicit checkpoints, but cancellation is delivered at the next
await. A fully blocking, CPU-bound or synchronous-I/O tool that never
yields to the event loop is not killed (ADR 0003's reactive caveat
still holds for that case). Implication: a pathological non-cooperative
tool can overrun the wall-clock budget until it yields or returns.
Tracking: `BL-155`.

## L12. Store decorators do not forward the extension Protocols

State: `EncryptedStore` and `ACLStore` implement the core `MemoryStore`
surface only. Wrapping a backend that also satisfies `BatchMemoryStore`,
`ScannableStore`, `ContentAddressableStore`, `CASMemoryStore`, or
`SweepableStore` does not expose those capabilities through the
decorator. Implication: an `isinstance(store, CASMemoryStore)` check
fails after decoration, so CAS, batch, scan, content-addressing, and
active sweep are lost when encryption or ACLs are layered on. Tracking:
`BL-156`.

## L13. DynamoDB TTL is integer-second granularity

State: `DynamoDBStore` stores expiry as integer seconds, truncating
sub-second TTLs; `InMemoryStore`, `SQLiteStore`, and `S3Store` keep
float seconds and `RedisStore` uses milliseconds. ADR 0004's
"sub-second precision is supported" does not hold for the DynamoDB
adapter. Implication: a sub-second TTL on DynamoDB rounds down (often
to "expire at the next integer second"), and read vs `compare_and_set`
absence can disagree at a second boundary. Tracking: `BL-157`.

## L14. Out-of-tree workload loading executes the bundle's Python

State: `load_workload_from_path` imports the target's `contract.py` and
`__main__.py`, executing module-level code, with no `allow_contract`
analog to the skill-install gate (ADR 0008). This is the intended L1/L2
contract (a workload is trusted code), but unlike skill install it has
no in-code gate. Implication: pointing the loader or `agents run` at an
untrusted directory runs its Python; only load trusted bundles.
Tracking: `BL-158`, `SECURITY.md`.
