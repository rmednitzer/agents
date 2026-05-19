# ADR 0013: Fifth code audit, additive hardening

- Status: Accepted
- Date: 2026-05-19
- Authors: rmednitzer
- Builds on: ADR 0001-0012

## Context

A fifth full in-depth audit of `harness`, `memory`, `skills`,
`workloads`, the CLI, `evaluation`, and the offline gates, run against
the same green gates (ruff, mypy strict, pytest at `cov-fail-under=94`,
schema-drift, the dispatch evaluation gate). The four prior audits
(ADR 0009-0012) closed a large surface; this pass targeted the *classes*
those audits fixed pointwise and the code paths exercised by the recent
major dependency bumps (`anthropic >=0.102`, `openai >=2.37`,
`redis >=7.4`).

The audit confirmed the prior fixes hold (BL-156/157/167/168/170/172/
173/177/181/182/183 re-derived clean) and found five new, previously
untracked issues. Consistent with ADR 0009-0012, the clear bugs are
fixed additively with regression tests in the same increment; this ADR
records the cross-cutting reasoning. The backlog tracks the line items
(`BL-188`-`BL-192`); the same ID discipline applies.

The recurring lesson, again: the prior audits fixed the read-vs-write
expiry-boundary class and the JSON-extraction DoS class at the specific
sites they inspected, not as an invariant swept across every sibling
path. This pass closes the remaining instances of those same classes.

## Decision

### 1. Read-vs-listing expiry boundary (BL-188)

`InMemoryStore` and `SQLiteStore` `read` / `sweep_expired` treat an
entry as live until `now > expires_at` (live at the exact expiry
instant): BL-168 even aligned SQLite's sweep to that boundary and its
fix comment *asserted* `list_keys`/`scan` already agreed. They did not:
both used `expires_at > now`, which excludes the entry at the exact
instant `now == expires_at`. So a key that `read` still returns, and
that `sweep_expired` still keeps, was missing from `list_keys` and
`scan` for one tick. This is the read-vs-CAS boundary class of
BL-157/168/177, unfixed for the *listing* paths of the two reference
in-tree adapters (the very adapters backend authors copy). Both paths
now use the `now <= expires_at` live boundary, and the misleading
BL-168 comment is corrected. DynamoDB and S3 were verified internally
consistent and are unchanged.

### 2. OpenAI batch error diagnostic loss (BL-189)

`OpenAIBatchProcessor.results` decoded an output-file row's
`response.status_code`; a request-level failure that OpenAI writes to
the *output* file with `response: null` and a structured `error`
(distinct from the error-file rows) fell to the non-200 branch and was
yielded as `error_type="http_None"`, discarding `line["error"]`. The
docstring's "the caller always sees every request" intent was met for
row *presence* but not for *diagnosability* on a billing-relevant bulk
path. The non-200 branch now prefers the structured `error.code` when
present, falling back to `http_<status>` only when there is no error
object. Additive: a normal 200 row and an error-file row are unchanged.

### 3. LocalSkillSource symlink-safe clear (BL-190)

BL-169 hardened `LocalSkillSource`'s copy loop (symlink refusal) and
BL-172 built the shared symlink-safe `_prepare_install_dir`, explicitly
naming `LocalSkillSource` the "twin" of the network-source clearing
hole, but only routed the network sources through it. `LocalSkillSource`
still cleared `dest/<name>` with a bare `shutil.rmtree`: on a
pre-existing `dest/<name>` *symlink* that raises an unhandled `OSError`
(rmtree refuses a symlink) instead of the framework's `SkillLoadError`,
and leaves the link in place. Not an escape (`shutil.rmtree` does not
follow the link to delete its target, and the post-copy `relative_to`
containment check still holds), so this is a robustness / clean-error /
defence-in-depth consistency fix, not a vulnerability. `LocalSkillSource`
now uses the same audited `_prepare_install_dir` as every other source:
one clear for all sources.

### 4. JSON-extraction span-list memory ceiling (BL-191)

BL-173/182 bounded the JSON extractor's parse *work* (per-candidate
bytes, cumulative parsed bytes) and the module comment argues (for the
*parse* path) against a candidate-count bound. A separate axis was
uncovered: `_balanced_spans` eagerly materialises one `(open, end)`
int-pair per closing bracket *before* `first_json_array` runs, so a
bracket-heavy body (`"[]" * n` from an untrusted model / MCP tool
result) costs an O(n) list of tuples (~120 B/pair, a ~30x amplification
over the source text) regardless of the parse budget, and the eager
list defeats the otherwise-fast first-candidate early return. A hard
`_MAX_SPANS` ceiling now bounds that list. This is a memory axis the
count-vs-work reasoning did not cover; overflowing it is adversarial
and correctly degrades to the existing malformed-input / DispatchError
contract (the same posture as the oversized-span and RecursionError
paths). The cap (65536) is far above any legitimate dispatch response
(a handful of brackets), so it never bites real input.

### 5. Provenance-gate registry value validation (BL-192)

`scripts/check_run_records.py` validated the `--registry` payload was a
JSON object but never its *values*. `RunRecord.contract_digest` is a
model-normalised lowercase 64-hex string, so a non-canonical registry
(a JSON number, an explicit `null`, uppercase hex) can never equal any
record's digest: the gate becomes silently unsatisfiable and reports a
nonsensical "does not match the registry digest 123" per-record message
instead of naming the malformed config. It already fails closed (no
forgery bypass), but the failure was unactionable. Registry values are
now validated as canonical lowercase 64-hex at load, mirroring the
per-record model strictness; a malformed registry is the documented
invocation failure (exit 2) naming the offending keys.

## Consequences

- The two in-tree reference memory adapters now satisfy a single,
  uniform live boundary (`now <= expires_at`) across `read`, `mget`,
  CAS, `read_versioned`, `list_keys`, `scan`, and `sweep_expired`. The
  boundary is now an audited invariant of the reference adapters, not a
  per-site property; backend authors copying them inherit the correct
  behaviour.
- The OpenAI bulk path surfaces actionable error codes for every
  failure mode OpenAI can emit, not only the error-file rows.
- Every `SkillSource` clears its install directory through one audited,
  symlink-safe helper; there is no longer a per-source clearing
  variant.
- The JSON extractor is bounded on both parse work *and* span memory;
  adversarial bracket floods cannot amplify into multi-GB resident
  sets.
- The offline provenance gate fails fast and legibly on a malformed
  registry instead of becoming silently unsatisfiable.
- All changes are additive and preserve L1 import paths and signatures
  (ADR 0007). The default control flow and exceptions are unchanged for
  every non-adversarial input; only the exact-expiry-instant listing
  result, the OpenAI null-`response` error label, the `LocalSkillSource`
  error *type* on a symlink, an adversarial span flood, and a malformed
  registry change observable behaviour, each toward correctness.

## Revisit triggers

- A new memory adapter that does not delegate expiry to `InMemoryStore`
  must be checked against the same `now <= expires_at` invariant across
  all of read / CAS / list / scan / sweep.
- If a future OpenAI SDK changes the batch output-file row shape (the
  `response` / `error` discriminator), revisit BL-189's branch.
- `_MAX_SPANS` is a memory ceiling, not a correctness parameter; if a
  legitimate workload ever produces a body with more than 65536
  balanced bracket pairs, raise the ceiling rather than removing it.
