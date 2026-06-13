# Vertex MCP: cross-pollination analysis

- Status: Analysis (informational; not an ADR, no decision adopted)
- Date: 2026-06-13
- Author: rmednitzer
- Tracking: `BL-242` .. `BL-249` (see `docs/backlog.md`)

## Purpose

A deep audit of what this substrate can learn from Vertex, a
long-running, single-operator MCP gateway that has been operating an
agent against real infrastructure, feeds, and a regulatory corpus over
many months. This repo is the opposite shape: a reusable,
Protocol-driven substrate refined mostly through static code audits
(14 of the 27 ADRs). The two have been converging on the same problems
from opposite ends, so the operator system is a useful mirror for the
gaps a substrate cannot see from the inside.

This document records the transferable patterns, each mapped to a
precise repo gap and an additive-to-L1 proposal (ADR 0007). It does not
adopt any of them; each is tracked as a backlog item for a maintainer
decision.

## Method

The audit inspected the gateway's own subsystems (its cross-session
memory schema, its hybrid retrieval pipeline, its graduated-autonomy
design note, its staleness-gated corpus tools, and its agent-reliability
runbook) and cross-checked each against the corresponding repo surface
(`harness/guard.py`, `harness/provenance.py`, `harness/runtime.py`,
`memory/semantic.py`, `memory/tiering.py`, `memory/compaction.py`, the
`evaluation/` gate). The gateway's private operational details (host
topology, addresses, credentials, absolute paths) are deliberately kept
out of this artifact; only the transferable patterns are recorded.

## Meta-finding: influence currently flows the other way

The first result of the audit is that the influence already runs
substrate to operator, not the reverse. The gateway's own reliability
runbook cites this repo as its known-good reference for retry and
backoff (`harness/runtime.py: RetryPolicy`), run provenance
(`RunRecord`, ADR 0012), and behavioural postconditions (the
contract layer). It learned its reliability primitives from here. The
value in the reverse direction is concentrated in the few places where
operating a real agent over long horizons against partially trusted
data forced patterns that a substrate refined through static audits has
not yet had to confront: graduated authority, retrieval quality,
output trustworthiness, and a cognitive schema over plain key/value
memory.

## Lessons, ranked by leverage and fit

| # | Lesson | Repo gap today | Item |
| --- | --- | --- | --- |
| 1 | Graduated authority by blast radius, plus tier classification | `GuardDecision` is a flat APPROVE / REJECT / REQUIRE_APPROVAL | `BL-242` |
| 2 | Hybrid retrieval: RRF fusion and an optional rerank stage | `InMemorySemanticStore` is vector cosine only (L5) | `BL-243` |
| 3 | DEGRADED disposition and grounding postconditions | `RunOutcome` has no shipped-but-degraded value; no anti-confabulation predicate | `BL-244` |
| 4 | Structured operational memory (threads, tasks, timeline, decisions) | Memory is key/value plus TTL; no cognitive schema | `BL-245` |
| 5 | Refusal as data, and read-side staleness gating | Guard rejects via string or raise; no `as_of` or freshness contract | `BL-246` |
| 6 | Decay-ranked forgetting and bitemporal facts | `demote_to_capacity` is FIFO; ADR 0024 defers LRU | `BL-247` |
| 7 | Graceful degradation ladder | `RetryPolicy` retries the same call; no fallback chain | `BL-248` |
| 8 | Scheduled single-shot workload and session rehydration | `agents run` is one-shot; no trigger or context refresh | `BL-249` |

### 1. Graduated authority by blast radius (`BL-242`)

**Pattern.** The gateway's design note defines a four-tier authority
model keyed to reversibility and blast radius, not to a binary "needs
approval". Tier 0 (read-only) requires no approval. Tier 1
(reversible, low blast) lets the model act, then log and notify. Tier 2
(stateful, visible) requires the model to prepare the action with
context and a rollback plan, then wait for explicit confirmation.
Tier 3 (irreversible, high blast) requires a two-step confirmation with
the parameters restated, and evidence capture before and after. The
load-bearing idea is that the model's critical function at each tier is
not execution but correct tier classification, something a static
allowlist cannot do because it cannot assess "this will cause a brief
blip on the load balancer, I should ask first". The note maps the tiers
to EU AI Act Articles 9, 12, 13, and 14.

**Repo gap.** `harness/guard.py` is exactly the flat shape the gateway
outgrew: `GuardDecision = {APPROVE, REJECT, REQUIRE_APPROVAL}`, with a
`severity` of HARD or SOFT only on REJECT. There is no notion of a
blast-radius tier, no rollback plan as a required field of an approval
request, no evidence capture for irreversible actions, and no
parameter-restatement second step. The repo's stated purpose is to
define authority boundaries between humans, agents, and tools, so this
is the most on-thesis gap in the audit.

**Additive proposal.** Extend `GuardResponse` with an optional `tier`
and an optional `rollback_plan` or evidence hook on REQUIRE_APPROVAL;
let a workload supply a `TierClassifier` Protocol (default behaviour
preserves L1, so no existing call site changes). The approval
interruption surface (`harness/interruption.py`, `ResumableState`)
already carries a proposal; a Tier 3 resume that restates arguments
composes directly with the (tool, arguments) binding re-verification
already shipped for deferred resume (`BL-193`, ADR 0027).

### 2. Hybrid retrieval: ship the fusion, keep the model pluggable (`BL-243`)

**Pattern.** The gateway's retrieval pipeline runs three stages:
parallel recall via keyword (FTS5) and vector search, a Reciprocal Rank
Fusion (RRF) merge of the two ranked lists (`score += 1 / (k + rank)`,
k = 60), and an optional cross-encoder rerank over the top candidates.
It uses a recall-then-rerank funnel (fetch several times the final
limit, rerank a smaller window, return the limit) and applies a
per-source weight.

**Repo gap.** `memory/semantic.py: InMemorySemanticStore.query_semantic`
is vector cosine only (`_cosine`), and `LIMITATIONS.md` L5 concedes that
"semantic quality and a durable vector backend are the workload's
integration". That framing conflates two separable things: the embedder
and reranker (genuinely vendor-bound, correctly out of tree) and the
fusion algorithm (pure arithmetic, no vendor). RRF is small,
deterministic, and dependency-free, the same profile as the references
the repo already ships (`HashingEmbeddingProvider`,
`TruncatingSummarizer`).

**Additive proposal.** A `fuse_rrf` helper plus an optional `Reranker`
Protocol beside the existing `Embedder`, and a hybrid query path that
combines a keyword pass with the vector pass. Vector-only stays the
default. This converts L5 from "quality is your problem" to "the fusion
and funnel are in tree, deterministic, and gated by the evaluation
harness; only the model weights are yours", a stronger and directly
testable claim under the existing `evaluation/` P@1 and MRR gate.

### 3. DEGRADED disposition and grounding postconditions (`BL-244`)

**Pattern.** The gateway's reliability runbook lands on two ideas this
repo does not have. First, a three-valued disposition (ok, degraded,
error): a degraded run still ships its artifact but prepends a banner
and emits a blocker event, and its exit code stays 0 (so a scheduler
does not flap) while a hard error exits non-zero. Second, grounding
postconditions that catch confabulation deterministically, for example
"every cited identifier of the form CVE-YYYY-N must appear in the
captured tool output" and "the artifact must carry at least one source
citation". These relabel output as degraded; they never rewrite model
content.

**Repo gap.** `harness/provenance.py: RunOutcome` enumerates terminal
outcomes (succeeded, output-invalid, the resumable case, and the
governance, approval, budget, and cancellation terminals) with no
"delivered but degraded" value, and `harness/recovery.py:
RecoveryOutcome.directive` is about control flow, not a disposition
label that travels with the output. The repo also ships no grounding or
citation postcondition as a reusable contract predicate, despite this
being the highest-value anti-hallucination check for a retrieval agent.

**Additive proposal.** Add a degraded disposition (on `RunOutcome` or as
a parallel `quality` field on the `RunRecord` already emitted per
ADR 0012), with the postcondition stage able to annotate-and-continue
rather than only pass or flag. Ship a reference
`grounding_postcondition(citation_pattern, tool_output)` predicate.
Defaults preserve today's binary behaviour.

### 4. Structured operational memory: the schema is the missing layer (`BL-245`)

**Pattern.** The gateway's cross-session memory is a typed state machine
whose tables are, in effect, memory types: sessions, checkpoints,
decisions, tasks (with a dependency DAG, a status FSM
pending to active to done, failed, or blocked enforced by an explicit
transition table, and an append-only task log), a categorized event
timeline, and threads (open loops carrying a next action, an owner, and
a stale-after window, surfaced by a "what have I dropped" staleness
query). It also keeps bounded core-memory blocks and a small
entity / fact knowledge graph.

**Repo gap.** The repo's memory is deliberately a substrate: key/value
plus TTL plus namespace, with extension Protocols (CAS, Versioned,
Transactional, Semantic, Sweepable). That is the right foundation, but
there is no cognitive schema on top of it: no first-class task with
dependencies, no open thread that can go stale, no decision log, no
categorized event timeline. Every workload that runs longer than one
shot will reinvent these, and the gateway is the existence proof of
what that layer looks like.

**Additive proposal.** A `memory` journal library offering typed
records (Task, Thread, Decision, Event) persisted through the existing
`MemoryStore` and `TransactionalMemoryStore` Protocols, so it inherits
namespace, TTL, audit, and encryption for free. The FSM-with-transition
-table and the stale-after query are the two patterns to copy first;
both are deterministic and testable. This is the largest lift in the
audit and plausibly its own ADR.

### 5. Refusal as data, and read-side staleness gating (`BL-246`)

**Pattern.** Two patterns in the gateway's corpus tools. First, every
tool returns a structured envelope (`{ok, refusal: {reason, detail}}`)
and audits it with an exit code, instead of raising or returning a bare
string; refusal is model-legible data. Second, staleness gating: reads
carry provenance (an as-of timestamp and a corpus baseline date), and
acting on stale or proposal-stage data requires explicit acknowledgement
(an `acknowledge_stale` or `acknowledge_proposal` flag), with withdrawn
data refused outright and derived analysis labelled as working notes,
not authoritative.

**Repo gap.** The repo took a step here with `BL-137`
(`soft_reject_as_error` raising a typed `ModelRetry`), but that is one
path, not a uniform contract. More important, its provenance is write
side: `RunRecord` records what a run did. There is no read-side
freshness contract, nothing that stamps a memory value with an as-of and
forces the agent to acknowledge staleness before relying on it. For an
agent acting on cached or retrieved facts, that is the difference
between confidently wrong on stale data and made to see the data is old.

**Additive proposal.** An optional `as_of` or freshness field on memory
reads (the `MemoryRead` event already exists) and a guard or contract
predicate `require_fresh(max_age)` that turns a stale read into a
REQUIRE_APPROVAL or a structured refusal. Pairs naturally with the
tiering in lesson 1.

### 6. Decay-ranked forgetting and bitemporal facts (`BL-247`)

**Pattern.** The gateway's fact store is bitemporal (validity time, when
a fact was true, separate from transaction time, when it was recorded
and when it was superseded) and carries a confidence. It implements an
Ebbinghaus-style forgetting score
(strength = importance times a recency decay times a reinforcement
factor in the access count) and prunes facts below a strength
threshold. A new value for the same subject and predicate auto-supersedes
the prior one and closes its validity window.

**Repo gap.** ADR 0024 (`memory/tiering.py:
TieredMemoryStore.demote_to_capacity`) ranks demotion candidates by
first-write insertion order, and `LIMITATIONS.md` L5 explicitly defers
LRU because "read tracking is not in the store contract". The gateway
shows the richer-but-still-deterministic signal the repo is missing: a
strength score from importance, recency, and access frequency. Nothing
in the repo's memory model captures validity over time, which matters
for any agent reasoning about a changing world.

**Additive proposal.** A ranking hook so `demote_to_capacity` can rank
by a strength function rather than FIFO (default stays FIFO, so ADR 0024
is unchanged), and a `BitemporalMemoryStore` extension Protocol for the
validity and supersession pattern. Both are deterministic and fit the
"Protocol plus reference impl first" cadence.

### 7. Graceful degradation ladder (`BL-248`)

**Pattern.** The gateway's scheduled pipelines degrade rather than fail:
a briefing pipeline falls back to a local headlines-only path when the
API is unavailable, and a sibling appliance states the principle
outright, that a fallback ladder degrades to a local mock on cap
exhaustion or call failure so a controller always gets an answer.

**Repo gap.** `harness/runtime.py: RetryPolicy` (`BL-136`) retries the
same call with backoff and a circuit breaker. That is resilience against
transients, not degradation: there is no first-class "try premium model,
then cheap model, then cached result, then static stub" ladder. The
gateway's runbook also flags the related fleet concern, a single shared
budget letting an exploratory agent starve a security-critical one,
which the repo's per-run `ActionBudget` has no cross-workload
arbitration for.

**Additive proposal.** A `FallbackChain` around the `Runtime` Protocol
(an ordered set of providers or models with a predicate for when to
descend), composing with `RetryPolicy` rather than replacing it. A
`LIMITATIONS` note on cross-workload budget reservation captures the
fleet concern, which is otherwise out of substrate scope.

### 8. Scheduled single-shot workload and session rehydration (`BL-249`)

**Pattern.** The gateway runs several single-shot, timer-scheduled
agents inside a hardened envelope (memory and CPU caps, encrypted
credential loading), and offers a session-start context refresh ("call
this first when resuming") with a delta mode that returns only what
changed since the last state commit.

**Repo gap.** `agents run <workload> <query>` is one-shot and
synchronous; there is no trigger or schedule model, and no cross-session
rehydration. `ResumableState` resumes a single paused approval leg, not
"here are my open threads, stale loops, and recent decisions" on a fresh
session. The schema in lesson 4 is the prerequisite for a real context
refresh.

**Additive proposal.** Mostly a reference workload and docs, not core,
plus a `context_pack(namespace)` helper that assembles open threads,
recent decisions, and stale items from the lesson 4 journal. The
hardened single-shot envelope is a deployment pattern to document, not a
contract change.

## What does not transfer

Several gateway capabilities are workload-level or
deployment-level and would be a category error in the substrate: the
domain tools (infrastructure status, intelligence feeds, the regulatory
corpus, document conversion), the host topology, and the nested
agent-invocation tool. The detached long-running session abstraction
(used to run work past a platform tool-call cap) is interesting against
`LIMITATIONS.md` L11 (the wall-clock watchdog preempts only at an await
boundary, so a blocking tool overruns), and a "detached long-running
tool" pattern is worth a note, but it is closer to a workload concern
than a contract change.

## Convergent validation

The gateway also confirms several repo choices by independent
convergence. Its run records log input keys and output sizes but never
raw output or secrets, the same discipline as the repo's `Redactor` and
`RedactingSink` (`BL-134`). Its modular tool registration with
deliberate tool removal echoes the repo's minimal-surface, additive-only
philosophy. And, as noted in the meta-finding, its reliability layer was
built by reading this repo's retry, provenance, and contract surfaces.

## Recommendation

If a maintainer takes three, take `BL-242` (graduated authority),
because it is the repo's literal thesis and the gateway has the more
mature model; `BL-243` (RRF fusion and an optional rerank stage),
because it converts L5 from a disclaimer into a tested in-tree
capability for about a day of work; and `BL-244` (degraded disposition
and grounding postconditions), because it is the cheapest real
improvement to output trustworthiness and extends contracts the repo
already ships.
