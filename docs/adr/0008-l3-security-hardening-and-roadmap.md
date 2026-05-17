# ADR 0008: L3 entry, security hardening, and validated roadmap

- Status: Accepted
- Date: 2026-05-17
- Authors: rmednitzer
- Builds on: ADR 0001-0007

## Context

The L2 wave (ADR 0007, PR #20) shipped the L1-to-L2 bridge: guard and
budget wiring, durable memory backends, observability surface,
composition, and skill installation. A subsequent deep analysis of the
repository against current agent-engineering practice (Anthropic agent
guidance, observability and evaluation, safety and supply chain, memory
and reliability) surfaced gaps in two classes:

1. Verified security issues exploitable today.
2. Capability gaps where the framework lags current practice. Most were
   already tracked as L3 (`BL-1xx`); several were not.

This ADR records the cross-cutting decisions for the first L3 increment:
the security hardening implemented now, and the restructured, externally
validated roadmap for the rest. Per-item state stays in
`docs/backlog.md`; this ADR is the why.

## Decision

### 1. Security hardening, implemented now, additive

Three changes land in this increment. All follow ADR 0007 section 1
(additive to L1; new optional keyword parameters whose defaults preserve
L1 behaviour; no L1 import path or signature removed):

- `skills.sources.GitHubSkillSource`: a hostile or corrupt archive is
  now bounded. New keyword-only parameters (`sha256`,
  `max_download_bytes`, `max_members`, `max_file_bytes`,
  `max_total_bytes`) with safe defaults cap the download, the member
  count, the per-member size, and the total uncompressed size, and
  optionally verify the tarball digest. Member path traversal was
  already defended; the new caps close the decompression-bomb and
  unbounded-read exposure that stdlib `tarfile` does not. The docstring
  states that a branch `ref` is mutable and that an immutable ref (a
  commit SHA or release tag) plus a `sha256` is the tamper-evident
  configuration.
- Skill contract execution is gated. `skills.loader.discover_skill`
  takes `allow_contract: bool = True` (the L1 default, in-tree skills
  are trusted, behaviour unchanged). When False, a present
  `contract.py` is refused by `Skill.contract()` rather than executed.
  `skills.sources.install_skill` defaults `allow_contract=False`: an
  installed bundle came from an untrusted source, so its `contract.py`
  is not executed unless the caller opts in. `install_skill` is L2
  surface, not L1, and a secure default at the network trust boundary
  is the correct posture; the L1 `discover_skill` default is unchanged,
  so no L1 behaviour regresses.
- `harness.redaction` adds `Redactor` and `RedactingSink`. Wrapping a
  sink scrubs sensitive argument names, secret-shaped values, and
  over-long scalars from every event before it reaches a downstream
  sink. Additive: no existing sink changes unless a caller opts in.

### 2. Defence in depth, not a sandbox

Gating contract execution and bounding archives reduces the blast
radius of an untrusted skill; it is not a sandbox. A skill that is
loaded with `allow_contract=True` still executes arbitrary Python. True
isolation (subprocess or container, capability scoping) remains an open
item (`BL-133`). The security model is stated in `SECURITY.md` and the
residual risk in `LIMITATIONS.md`.

### 3. Roadmap is tiered and externally validated

`docs/backlog.md` L3 is restructured into priority tiers (Tier 0
security, Tier 1 AI-quality, Tier 2 reliability and observability,
Tier 3 governance, Tier 4 release and operations). Items not previously
tracked are added in the `BL-130+` range (evaluation harness, semantic
memory, prompt caching, structured tool error, retry and backoff,
secrets redaction, prompt-injection posture, OTel GenAI conventions,
release and operations). The `BL-054`/`BL-112` and `BL-090`/`BL-121`
scope contradictions are reconciled in place.

Each capability claim was cross-checked against a primary source before
the backlog was edited; the sources are recorded in
`docs/backlog.md` ("Sources consulted") with access dates.

### 4. CI hardening

A CodeQL workflow is added (static security analysis on push, pull
request, and a weekly schedule). The test job runs a Python 3.12 and
3.13 matrix so the `>=3.12` and 3.13 classifier claims are exercised.
GitHub Actions are tag-pinned and Dependabot already proposes updates
for `pip` and `github-actions`; commit-SHA pinning and a blocking
dependency-audit gate are tracked (`BL-150`). `gen_schema.py --check`
is already enforced by the test suite (`tests/workloads/test_schema.py`)
and therefore by CI; no redundant workflow step is added.

## Consequences

Positive: the exploitable issues are closed without an L1 break; the
roadmap is coherent, prioritised, and grounded in primary sources;
governance and release maturity are now tracked and partly delivered.

Negative: `install_skill` now refuses a bundled `contract.py` by
default. This is intentional and documented; callers that relied on
auto-execution pass `allow_contract=True`. The redaction default set is
heuristic and may both miss a bespoke secret and clamp a long benign
value; it is opt-in and tunable.

Neutral: a single ADR for the increment, as in ADR 0007. Per-item
rationale stays in docstrings and `docs/backlog.md`.

## Revisit triggers

- A skill ecosystem emerges that needs true per-skill isolation
  (revisit the gate-only model; promote `BL-133`).
- The redaction heuristic produces material false negatives in audit
  evidence (revisit the default pattern set).
- An L1 Protocol must change to land an L3 item (write a dedicated ADR;
  do not fold it here).
