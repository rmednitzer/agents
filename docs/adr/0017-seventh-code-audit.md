# ADR 0017: Seventh code audit, additive hardening

- Status: Accepted
- Date: 2026-05-23
- Authors: rmednitzer
- Builds on: ADR 0001-0016

## Context

A seventh full in-depth audit of `harness`, `memory`, `skills`,
`workloads`, the CLI, `evaluation`, and the offline gates, run
against the same green gates (ruff, ruff format, mypy strict,
pytest at `cov-fail-under=94`, schema-drift, REUSE 3.x, `pip-audit`,
the dispatch evaluation gate at P@1 = MRR = 1.0). The prior audits
(ADR 0009 / 0010 / 0011 / 0013 / 0015) and the post-ADR-0015
deferred-close (BL-209-BL-211) closed a large surface, and ADR 0014
(BL-180 durable Versioned + Transactional) and ADR 0016 (BL-133
skill execution isolation) extended the capability surface. The
recent BL-212 / BL-213 / BL-214 wave (sweeper size bound on
InMemoryStore, SQLiteStore, and Redis) shipped between ADR 0016 and
this audit, with PR #60 surfacing two real concerns at code review
(client-clock score source and non-atomic write+index) that landed
their fixes in the same merge.

This audit targeted the *classes* the prior audits fixed pointwise,
the code paths exercised by the BL-212-214 wave, and the new IPC
surface introduced by ADR 0016 (`BL-133`). It confirmed the prior
fixes hold and found four new, previously untracked issues spread
across `skills/` (three) and the `read_text` encoding boundary
across `workloads/` / `evaluation/` (one). Consistent with the
prior audits, every clear bug is fixed additively with a regression
test in the same increment; this ADR records the cross-cutting
reasoning. The backlog tracks the line items (`BL-215` through
`BL-218`).

The recurring lesson, again: every new IPC / decode boundary the
codebase adds is a re-instance of a class the earlier audits closed
elsewhere, so the next audit's job is to verify the class
generalises. ADR 0016 (`BL-133`) introduced a length-prefixed
parent-child IPC surface and a metadata frame parser; both are
class extensions of the "malformed external input must not crash
the audited path" invariant from `BL-167` / `BL-200` / `BL-201`.

## Decision

### 1. SkillLoader UTF-8 decode boundary (BL-215)

A BL-204 class extension on the loader-input leg. `parse_skill_md`
called `path.read_text(encoding="utf-8")` and caught `OSError` but
not `UnicodeDecodeError`. A SKILL.md that is not valid UTF-8
(latin-1, a binary file misnamed, a UTF-16 BOM at the head of the
stream) raised `UnicodeDecodeError` instead of the documented
`SkillLoadError`, leaking a Python-internal exception past the
loader's exception boundary. `_read_body_only` (the
`Skill.body()` lazy loader) had the same gap on a different path.
Both now catch `UnicodeDecodeError` and re-raise as
`SkillLoadError(path, "not valid UTF-8: ...")`, matching the
"unreadable file" branch of the documented contract. Two regression
tests pin the boundary on `parse_skill_md` (UTF-16 BOM and a
latin-1 high byte), one pins it on `_read_body_only`, and a sanity
test confirms the happy path still loads.

### 2. Subprocess IPC frame length bound (BL-216)

A new class introduced by ADR 0016 (`BL-133`): the parent-child IPC
on the `SubprocessSkillContractExecutor` uses a 4-byte big-endian
length prefix, capable of encoding up to ~4 GiB. The original
implementation read the length and called `stream.read(n)` with no
upper bound; a compromised child subprocess (the threat model the
executor was built for) writing a header claiming `2**32 - 1` body
bytes would drive the parent into a ~4 GiB allocation before
discovering the truncation, exhausting host memory.

The fix caps frame bodies at 64 MiB on both sides. The parent
(`skills.execution._read_frame`) raises
`SkillContractExecutorError` so the subprocess is killed at the
documented exception boundary. The child
(`skills._executor_child._read_frame`) treats an oversize header
as EOF so the main loop exits cleanly (defence in depth on the
trusted-input side: a corrupted pipe or a future parent-side bug
should not OOM the child either). The 64 MiB ceiling is generous
for any realistic frame (small JSON metadata, `{"ok": ...}` / error
responses, or pickled predicate request + workload-defined state)
and small enough that a malicious frame cannot exhaust the host.
Six new tests pin the parent-side reject path, the child-side EOF
treatment, the legitimate-small-frame happy path, and the
at-the-cap boundary (allowed) versus strictly-over (rejected).

### 3. Subprocess metadata validation (BL-217)

A class extension of `BL-159` / `BL-205` (non-finite numeric
coercion at the model-output trust boundary) on the IPC metadata
frame from ADR 0016. The child sends `{"name": str, "severity":
str, ...}` items in its metadata response; the parent's `_proxies`
closure inside `SubprocessSkillContractExecutor.load` used bracket
indexing (`item["name"]`, `item["severity"]`) and called
`Severity(sev_str)` directly. A malformed item (missing key,
non-string value, an unknown severity string from a buggy or
malicious child) leaked the underlying `KeyError` or `ValueError`
past the documented `SkillContractExecutorError` boundary. The
parent now validates every item structurally before construction:
non-dict → reject; missing key → reject; non-string `name` /
`severity` → reject; unknown severity → reject. Each failure path
raises `SkillContractExecutorError` with a diagnostic naming the
slot and the failure mode. Five regression tests pin every reject
case (missing name, unknown severity, non-dict item, non-string
name) and the happy path.

### 4. read_text UTF-8 encoding consistency (BL-218)

A documentation-and-consistency fix, not a security-critical bug,
but an audit-class hit nonetheless. The project standard is to
specify `encoding="utf-8"` on every `Path.read_text` call (the
`check_run_records` / `gen_schema` / `skills.loader` precedents).
Three call sites omitted the parameter:
`workloads.loader._build_loaded_workload` (manifest read),
`evaluation.dataset.load_dispatch_golden` (golden-set read), and
`workloads/_example/__main__.py` (the example workload's input
read). On a system with a non-UTF-8 locale (Windows cp1252, a C
locale ASCII), a manifest or golden-set carrying non-ASCII
content (a `description` field with an accented character, a
golden-set query in CJK) would silently mis-decode. The fix is the
explicit `encoding="utf-8"` argument on all three call sites; no
new test is needed because the existing read paths already exercise
the call sites and an encoding regression would surface as a
collation / equality mismatch in the existing tests.

## Consequences

- Every fix is additive to the L1 Protocols (ADR 0007). No public
  signature changed; no caller behaviour changed on the happy
  path. The exception types raised on the documented boundary
  (`SkillLoadError`, `SkillContractExecutorError`) are existing
  types, not new ones, so callers that already catch them gain
  the new precision without code change.
- 15 new regression tests (`tests/skills/test_bl215_loader_unicode.py`,
  `tests/skills/test_bl216_subprocess_frame_bound.py`,
  `tests/skills/test_bl217_subprocess_metadata_validation.py`).
- The `read_text` consistency fix (BL-218) has no new tests because
  the existing test suite exercises every changed call site and a
  regression would manifest as an encoding mismatch in those tests.
- Coverage stays at 94.97% (above the 94% gate, up from 94.89%).
- The IPC trust boundary (`BL-133` / ADR 0016) is now hardened to
  the same "external-input-must-not-crash" invariant the
  audit-path side (`BL-167` / `BL-200`) and the bulk-decode side
  (`BL-201`) already meet.

## Revisit triggers

The open items this audit deliberately did not touch:

- `BL-120` (live reference workload). Needs a funded provider key.
- `BL-132` / `BL-171` (prompt caching on the runtime adapter).
  Upstream-dependent on a verified PydanticAI provider-cache API
  plus a live model to validate.
- `BL-113` / `BL-138` (true OTel spans + GenAI semantic
  conventions). Upstream-dependent on the OTel logs SDK GA.
- `BL-114` (deeper PydanticAI resume). Upstream-dependent.
- `BL-135` open half (compaction / summarisation / tiering, plus
  `BoundedSweepableStore` on `DynamoDBStore` / `S3Store`).
  In-tree work; the size-bound on the remaining two durable
  adapters needs an auxiliary timestamp attribute (DynamoDB) or
  timestamp-prefixed object (S3) since neither has the native
  ordering SQLite (rowid) or Redis (sorted set) provide.
- `BL-150` (commit-SHA pinning of GitHub Actions). Maintainer or
  Dependabot action.
- `BL-155` (true wall-clock preemption). Needs a thread/process
  execution boundary; the asyncio `await`-based watchdog is the
  documented preempt-at-yield-point shape.
- `BL-179` (`RetryPolicy` partial usage accounting). Upstream-
  dependent on PydanticAI exposing partial usage on exception.
- BL-214 atomicity (write + index ZADD aren't atomic) and BL-214
  chunked sweep (`_members()` does `ZRANGE 0 -1`). Both flagged
  by PR #60 review; both tracked under `BL-135`'s remainder
  because the fix (Lua script for atomicity, ZSCAN-style chunked
  iteration) is a follow-up design pass, not a same-PR fix.
