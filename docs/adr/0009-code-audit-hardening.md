# ADR 0009: Full code audit, additive hardening, errata

- Status: Accepted
- Date: 2026-05-17
- Authors: rmednitzer
- Builds on: ADR 0001-0008

## Context

A full in-depth audit was run across `harness`, `memory`, `skills`,
`workloads`, the operator CLI, and the documentation set, against the
quality gates (ruff, mypy strict, pytest, 94% coverage) which were and
remain green. Gates verify code shape, not behaviour, so the audit read
the logic directly. It surfaced three classes of finding:

1. Correctness and security bugs the gates and tests did not catch.
2. Capability and robustness gaps that are real but better tracked than
   rushed.
3. Documentation drift: factual errors, stale references, and one
   self-contradicting convention.

This ADR records the cross-cutting decisions. Per-item state is in
`docs/backlog.md` (`BL-154`-`BL-161`); residual risk is in
`LIMITATIONS.md` (L10-L14). This ADR is the why.

## Decision

### 1. Fix the clear bugs now, additively

Following ADR 0007 section 1 (additive to L1; no L1 import path or
signature removed; defaults preserve L1 behaviour), these landed in this
increment with regression tests (`BL-159`):

- `skills.embeddings.cosine_similarity` returns `0.0` for a non-finite
  norm or score. A NaN component otherwise survives `min`/`max`
  clamping as confidence `1.0` (NaN compares false, so the clamp keeps
  the bound), sorting an adversarial or buggy skill to the top of
  embedding dispatch.
- `skills.dispatchers._json.first_json_array` is a single linear pass.
  The prior nested-restart scan was O(n^2); a megabyte of `[` in model
  output hung the dispatcher. The contract is unchanged (first balanced
  top-level array); existing tests pass.
- The LLM and skill-based dispatchers reject a `bool` `confidence`
  (`isinstance(True, int)` is `True`; `float(True)` is `1.0`).
- `memory.EncryptedStore` and `memory.ACLStore` validate keys before any
  keyed operation, as the `MemoryStore` Protocol already requires of
  implementations. For `EncryptedStore` this also closes an AAD
  cross-key collision when a key contains the `::` separator.
- `harness.redaction.Redactor` walks every event field, not only
  dict-valued ones. A strict superset of the prior behaviour: it never
  un-redacts; it catches a secret-shaped or over-long value in a
  top-level string or list field too.

### 2. Track, do not rush, the capability gaps

True budget accumulation across an approval pause (`BL-154`), true
wall-clock preemption of a blocking tool (`BL-155`), decorator
forwarding of the extension Protocols (`BL-156`), DynamoDB float TTL
(`BL-157`), and the deferred skill-install / memory-adapter / CLI
hardening (`BL-161`) each touch a contract or a backend in a way that
warrants its own change, not an audit-pass drive-by. They are tracked
and their current behaviour is documented in `LIMITATIONS.md` L10-L14
so the boundary is explicit rather than surprising.

### 3. ADRs are immutable; record errata forward

ADR 0005 and ADR 0006 carry factual errata: ADR 0006 references the
example skill as `skills/_example/` but the directory is `skills/example/`
(manifest `name: example`); ADR 0006 says "five reference dispatchers"
but the L2 wave (ADR 0007) added `MultiDispatcher` and
`EmbeddingDispatcher`, so eight ship plus the `InstrumentedDispatcher`
wrapper; ADR 0005's "deferred to L2 / out of scope for L1" items
(name-match validator, out-of-tree loading, schema generation,
`workloads list`, skill resolution) all shipped in the L2 wave. Per
`docs/adr/README.md` ("ADRs are immutable once Accepted; a later ADR
supersedes an earlier one rather than editing it"), these are corrected
here and in the live docs (component docstrings, README), not by editing
0005/0006.

### 4. Documentation accuracy and one convention fix

The factual-drift fixes (`BL-160`): README dispatcher count;
`docs/runtime-providers.md` stale line citations; the `BL-130` ->
`BL-134` reference in `harness.redaction`; the "five reference
dispatchers" docstrings; the `workloads.manifest` "Phase 5" tense; the
wall-clock watchdog "preemptive" wording (it preempts at an await
boundary, not unconditionally; ADR 0003's reactive caveat still holds
for a blocking tool). `SECURITY.md` gains the prompt-injection /
untrusted-content posture (`BL-139` resolved). `CLAUDE.md`'s markdown
rule now exempts code spans, which it always did in practice.

## Consequences

Positive: the exploitable and silently-wrong paths are closed without an
L1 break; the remaining gaps are explicit and tracked; the docs match
the code; the prompt-injection posture is stated.

Negative: `Redactor` now also clamps over-long top-level scalar string
fields and may scrub a secret-shaped benign value there; it is opt-in
(`RedactingSink`) and tunable, and no shipped event carries such a
field today. The single-pass extractor only returns depth-zero arrays;
this matches the documented "top-level array" contract and all tests,
but a caller that relied on the old behaviour of also yielding a nested
array as an independent span would see a difference.

Neutral: one ADR for the audit increment, as in ADR 0007 and ADR 0008.
Per-item rationale stays in `docs/backlog.md` and docstrings.

## Revisit triggers

- A shipped event gains a free-text top-level string field that must
  not be clamped (revisit the redaction default for that field).
- A workload needs human-in-the-loop approval with a hard cumulative
  budget (promote `BL-154` / `BL-114` before that workload ships).
- An L1 Protocol must change to land an audit item (write a dedicated
  ADR; do not fold it here).
