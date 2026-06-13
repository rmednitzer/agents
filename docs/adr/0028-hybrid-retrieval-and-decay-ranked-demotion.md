# ADR 0028: Hybrid retrieval fusion and decay-ranked demotion (BL-243, BL-247)

- Status: Accepted
- Date: 2026-06-13
- Authors: rmednitzer
- Builds on: ADR 0001, ADR 0004, ADR 0011 (BL-131), ADR 0024 (BL-235)

## Context

The `docs/analysis/vertex-mcp-lessons.md` audit (merged in #114)
compared this substrate against a long-running, single-operator MCP
gateway and found two in-tree memory gaps where the substrate shipped a
primitive but left the quality layer entirely to the workload:

1. Retrieval (BL-243). `InMemorySemanticStore.query_semantic` (BL-131)
   ranks by vector cosine alone. `LIMITATIONS.md` L5 conceded that
   semantic quality is the workload's integration, but that framing
   conflated two separable things: the embedder and reranker (genuinely
   vendor-bound, correctly out of tree by ADR 0001) and the *fusion
   algorithm* that combines a keyword pass with a vector pass (pure
   arithmetic, no vendor). The operator gateway runs a keyword pass
   beside the vector pass, merges them with Reciprocal Rank Fusion, and
   reranks with an optional cross-encoder. The fusion is the piece a
   workload should not have to re-derive.

2. Demotion ranking (BL-247). `TieredMemoryStore.demote_to_capacity`
   (BL-235) ranks demotion candidates by first-write insertion order
   (FIFO), and L5 deferred LRU because read tracking is not in the store
   contract. A workload that does track a strength signal (importance,
   recency, access frequency) had no way to drive demotion by it: it was
   FIFO or nothing.

Both fixes are additive to L1 (ADR 0007): new optional surfaces beside
the existing ones, defaults preserving prior behaviour byte-for-byte.

## Decision

### BL-243: ship the fusion, keep the models pluggable

New module `memory/retrieval.py`:

- `fuse_rrf(*rankings, k=60)`: pure Reciprocal Rank Fusion over any
  number of ranked id lists. An id's fused score is the sum, over the
  lists it appears in, of `1 / (k + rank)`. Deterministic,
  dependency-free, ties broken by id, a duplicate within one list
  counted at its first position. `k` must be positive.
- `lexical_overlap_scores(query, documents)`: a deterministic
  token-overlap keyword baseline (the `HashingEmbeddingProvider`
  stance, BL-110), so the in-tree hybrid path needs no FTS engine. A
  model-quality keyword index (SQLite FTS5 bm25) satisfies the same role
  and is the workload's choice.
- `Reranker` Protocol: the cross-encoder analogue of `Embedder`. The
  model is injected (ADR 0001); a deterministic reranker satisfies the
  Protocol, a model-quality cross-encoder is out of tree.
- `HybridSemanticStore(SemanticMemoryStore)` Protocol: a separate
  extension Protocol (ADR 0004 "don't fake it") for a backend that can
  run a keyword pass beside the vector pass and fuse them.
- `HybridHit`: a result type whose `score` is a ranking signal (the RRF
  sum, or the reranker's relevance score), deliberately distinct from
  `SemanticHit.score`'s calibrated cosine.

`InMemorySemanticStore` retains the indexed source text (in lockstep
with the vector index) and gains `query_hybrid`: a vector pass and a
lexical pass over the live keys, fused with `fuse_rrf`, then an optional
rerank over a recall-then-rerank window. Vector-only retrieval stays
`query_semantic`.

### BL-247: a pluggable demotion ranking hook

`TieredMemoryStore.demote_to_capacity` gains an optional
`rank_key: Callable[[str], float] | None = None`. `None` (the default)
keeps the BL-212 / BL-224 first-write FIFO order byte-for-byte; when
supplied, keys are demoted in ascending `rank_key` order (ties
lexicographic), so a workload drives demotion by a strength score
without the store tracking reads.

`memory.tiering.decay_strength(importance, age_seconds, access_count, *,
half_life_seconds, reinforcement)` is the deterministic forgetting
reference (the operator-gateway Ebbinghaus formula): importance halves
every `half_life_seconds` and is reinforced by access count. Pass it as
`rank_key` to demote the weakest keys first. Its numeric inputs are
validated finite and non-negative at the call (the BL-159 / BL-231
non-finite-control class), so a NaN or negative input cannot silently
subvert the demotion order.

### Scope held out of this change

- The `BitemporalMemoryStore` Protocol (validity time vs transaction
  time, supersession, confidence) that BL-247 also named stays out: it
  is a new store Protocol plus a reference adapter, a separate increment
  on the BL-072 / BL-124 "Protocol plus reference first" cadence
  (tracked forward in `docs/backlog.md`).
- A durable hybrid adapter (an FTS5 keyword index beside a durable
  vector store) stays the workload's integration, like the durable
  semantic adapter (L5). The in-tree reference is in-memory.

## Consequences

- L5 narrows: the fusion and funnel (RRF, the recall-then-rerank window,
  the lexical baseline) are now in tree and deterministic; only the
  embedder and the optional reranker stay the workload's models. The
  demotion order is no longer FIFO-only.
- No L1 change. `query_semantic`, `demote_to_capacity()` with no
  `rank_key`, and every existing call site are byte-for-byte unchanged;
  `query_hybrid`, `rank_key`, `decay_strength`, and the new module
  symbols are purely additive.
- Blast radius: `memory` only. `memory/retrieval.py` (new),
  `memory/semantic.py` (retains text, adds `query_hybrid`),
  `memory/tiering.py` (adds `decay_strength` and the `rank_key` hook),
  `memory/__init__.py` (exports). No adapter, harness, or schema change.
  Rollback: revert the commit; no state or schema migration.
- Tests: 32 new cases (`tests/memory/test_bl243_hybrid_retrieval.py`,
  `tests/memory/test_bl247_demotion_ranking.py`); `memory/retrieval.py`
  at 100% line coverage.

## Revisit triggers

- A workload needs a durable hybrid store: lift the in-memory reference
  to a durable adapter with an FTS keyword index (the L5 durable-vector
  revisit trigger, now also durable-keyword).
- The `BitemporalMemoryStore` Protocol lands (the held-out BL-247 half).
- A model-quality reranker is exercised against a live workload (couples
  to BL-120, the same gate as the embedder).
