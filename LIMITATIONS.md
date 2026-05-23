# Limitations

Explicit scope boundaries and known gaps. Each limit states the current
state, the implication, and the tracking item. This is pre-1.0
infrastructure; the list is expected to shrink as L3 lands. Last
reviewed: 2026-05-23.

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
`pip` and `github-actions`; CodeQL (ADR 0008) and a blocking
dependency-audit gate (`pip-audit` over the exported lockfile, wired
into `ci-success`; ADR 0010) run in CI; the repo is REUSE 3.x compliant
(`reuse lint` gated); the `release` workflow emits a CycloneDX SBOM and
attests build provenance. Remaining: GitHub Actions are tag-pinned not
commit-SHA-pinned (the run environment cannot resolve third-party
action SHAs; deferred, not faked) and there is no signed
publish-to-index. Implication: not yet SLSA Build L2. Tracking:
`BL-150` (SHA pinning remainder), `BL-151`.

## L5. Semantic memory is in-tree; compaction/tiering is not

State: the `SemanticMemoryStore` extension Protocol and
`InMemorySemanticStore` reference ship (`BL-131`, ADR 0011): vector
write plus similarity query, reusing the `HashingEmbeddingProvider`
(`BL-110`) through memory's own `Embedder` Protocol. The shipped
embedder is a deterministic lexical baseline, not a semantic model (a
model-quality embedder satisfies the same `Embedder` Protocol and is
the workload's choice, out-of-tree by the ADR 0001 stance). There is
still no summarisation or tiering, and the durable adapters do not
implement `SemanticMemoryStore`. Implication: in-tree just-in-time
retrieval works for a single process; long-horizon compaction and a
durable vector backend are open. Tracking: `BL-135` (compaction /
tiering), `BL-131` notes the embedder scope.

## L6. The behavioural gate is deterministic-only

State: the evaluation harness (`evaluation/`, `scripts/eval.py`)
measures dispatch P@1 / MRR over a golden set and contract terminal
outcomes over a trajectory fixture, and a blocking CI `evaluation` job
in the `ci-success` aggregate fails on a routing regression (`BL-130`,
ADR 0011). The in-tree gate is deterministic and network-free (the
`KeywordDispatcher` and a stub runtime); an LLM-dispatcher or live
runtime trajectory suite is not yet gated. Implication: deterministic
routing/contract regressions are caught; live-model behaviour is not
yet measured in CI. Tracking: `BL-120` (the credentialed suite lands
with the live-model workload).

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

## L9. No prompt caching

State: `ActionBudget` now has a cost dimension and per-tool token /
wall-clock caps (`BL-123`, ADR 0010: `max_cost_usd`,
`max_tokens_per_tool`, `max_wall_clock_seconds_per_tool`,
`consume_cost`), so per-run spend can be bounded once an adapter
reports cost. The adapter still exposes no prompt-cache control:
prompt caching needs a verified PydanticAI provider-cache API and a
live model, so it is deferred (a no-op flag would breach the
no-half-implementation bar). Implication: a repeated stable
tools/system prefix still pays full token cost. Tracking: `BL-132` /
`BL-171`.

## L10. Approval-pause resume is a replay (budgets now accumulate)

State: budgets now accumulate across a pause (`BL-154`, ADR 0010):
`ResumableState` carries the consumed totals and the resumed
`BudgetTracker` is seeded from them, so tokens, steps, tool calls,
per-tool quotas, and cost no longer reset to zero per pause. The
PydanticAI resume is still a replay (`BL-114`): non-approval tool calls
re-execute on the replayed run (and now re-charge the cumulative
budget, so a non-idempotent tool runs again and the total is the sum
of all legs, not a deduplicated total). Implication: an approval-gated
workload's tool calls must be idempotent, and the cumulative budget
must allow for the replayed legs; `completed_actions` /
`ActionRecord` remain reserved for the non-replay resume. Tracking:
`BL-114` (eliminate the replay).

## L11. Wall-clock watchdog preempts only at an await boundary

State: the `asyncio.wait_for` watchdog enforces `max_wall_clock_seconds`
without explicit checkpoints, but cancellation is delivered at the next
await. A fully blocking, CPU-bound or synchronous-I/O tool that never
yields to the event loop is not killed (ADR 0003's reactive caveat
still holds for that case). Implication: a pathological non-cooperative
tool can overrun the wall-clock budget until it yields or returns.
Tracking: `BL-155`.

## L12. Use `wrap_*` to forward extension Protocols through a decorator

State: the bare `EncryptedStore(...)` / `ACLStore(...)` constructors
still expose only the core `MemoryStore` surface (L1/L2 compatibility
unchanged). The factories `memory.wrap_encrypted` / `memory.wrap_acl`
(`BL-156`, ADR 0010) compose a decorator that forwards exactly the
extension Protocols the wrapped backend satisfies, so
`isinstance(wrapped, CASMemoryStore)` is truthful. One deliberate
exception: `wrap_encrypted` does not forward `CASMemoryStore` even over
a CAS backend, because AES-GCM's per-write random nonce makes a
ciphertext-equality compare-and-set unrepresentable; encryption over a
CAS backend drops CAS by design (documented in `memory/README.md`),
rather than faking it. Implication: layer capability-rich backends with
the `wrap_*` factories, not the bare constructors; CAS-under-encryption
is unavailable.

## L13. DynamoDB native TTL sweep is integer-second (lazy expiry is float)

State: `DynamoDBStore` now stores `exp` as float seconds and uses a
float `:now` in its CAS conditions (`BL-157`, ADR 0010), so the
adapter's own lazy expiry, `read`, `scan`, `sweep_expired`, and
`compare_and_set` honour a sub-second TTL and agree at a second
boundary, matching the other adapters. DynamoDB's *native* server-side
TTL sweep still reads only the integer part of `exp` and is
best-effort (lagging up to ~48 h) regardless. Implication: a
sub-second TTL is honoured by the adapter; the provider's own
background sweep is coarse and lagging (already the documented model).
No tracking item (resolved).

## L14. Out-of-tree workload loading executes the bundle's Python

State: `load_workload_from_path` and `load_workload_from_entry_point`
(`BL-121`, ADR 0010) import the target's `contract.py` and
`__main__.py`, executing module-level code, with no `allow_contract`
analog to the skill-install gate (ADR 0008). This is the intended L1/L2
contract (a workload is trusted code), but unlike skill install it has
no in-code gate. Implication: pointing the loader or `agents run` at an
untrusted directory, or resolving an untrusted installed-package entry
point, runs its Python; only load trusted bundles. Tracking: `BL-158`,
`SECURITY.md`.

## L15. RetryPolicy counts tokens/steps from the final attempt only

State: with an opt-in `RetryPolicy`, wall-clock is bounded end to end
and tool-call / per-tool quotas are fed live from the gate, so those
dimensions hold across retries. Token and step usage is charged from
the *final* attempt's `result.usage` only: PydanticAI raises without
exposing partial usage on a failed `agent.run()`, so a failed
attempt's model round-trips are not counted (`BL-179`, ADR 0011).
Implication: a retrying run can exceed `max_tokens` / `max_steps` by
the failed legs' usage; bound a retrying run by the wall-clock or
tool-call dimension if a hard token ceiling matters. Closing this
needs upstream partial-usage on the exception path (the same
upstream-dependent shape as `BL-114` / `BL-132`). Tracking: `BL-179`.

## L16. Versioned-encryption legacy migration is current-key only by default

State: adopting a `VersionedKeyProvider` on a store previously sealed
by a plain `KeyProvider` works for values whose key is the versioned
provider's *current* version: the plain and versioned on-disk formats
have no distinguishing marker, so the authenticated legacy fallback
(`BL-181`, ADR 0011) retries a non-envelope value as legacy `nonce+ct`
with the current key only (AES-GCM authentication guarantees no silent
wrong value). Implication: the default migration contract is to seed
the key ring with the existing key as the current version and rewrite
values before rotating away from it; legacy data still under a key the
provider has already rotated past is not reachable for a default
legacy read (re-encrypt it through the old store first).

Opt-in lift (`BL-196`, runbook 7.4 #4): pass `legacy_multi_key=True`
to `EncryptedStore` / `wrap_encrypted` over a provider that also
implements `IterableKeyProvider` (the in-tree `RotatingKeyProvider`
does) and the legacy fallback iterates each historical key in the
ring instead of trying only the current one. AES-GCM authentication
still gates every attempt (false-tag probability ``2**-128`` per key,
accumulated ``N * 2**-128`` across the ring), so the multi-key
fallback never returns a wrong plaintext; the cost is up to ``N``
decrypt attempts on a true mismatch. The default is unchanged so a
KMS-backed provider that charges per call is not exposed to extra
cost; opt-in is the right shape for an in-memory or local ring.
New stores and already-versioned stores are unaffected. No tracking
item (a documented operational contract, not a defect).

## L17. DynamoDB versioned writes need a one-time row upgrade

State: `DynamoDBStore._item` now stamps a `ver` attribute (the
content-hash of `v`) on every write path (`BL-180`, ADR 0014), so
`write_versioned` and `delete_versioned` can use a one-round-trip
conditional expression on `ver`. A pre-BL-180 row was written without
`ver`; `read` / `mget` / `list_keys` / `scan` / `read_versioned`
continue to round-trip such a row (the read path hashes the live `v`
for path-independence), but `write_versioned(expected_version=...)`
against a row with no `ver` attribute fails its conditional
(`attribute_not_exists(ver)` evaluates true), so the call returns
`None` rather than silently succeeding. Implication: the migration
contract is to perform a single plain `write()` per legacy row, which
restamps `ver`; subsequent versioned writes succeed normally.
Conceptually parallel to L16's authenticated-legacy-fallback for
encryption, but without an in-line fallback because no atomic
"compute-hash-server-side" primitive exists in DynamoDB (and the
plain bytes can always be re-hashed by a fresh write, so the cost is
bounded). No tracking item (a documented operational contract, not a
defect).
