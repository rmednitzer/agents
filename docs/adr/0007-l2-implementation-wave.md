# ADR 0007: L2 implementation wave

- Status: Accepted
- Date: 2026-05-16
- Authors: rmednitzer
- Builds on: ADR 0002-0006

## Context

ADRs 0002-0006 shipped L1: contract surface, budgets/MCP/guard
surface, memory namespace contract, workload bundles, skills +
dispatchers. Each deferred a set of items to L2, consolidated in
`docs/backlog.md` (BL-001 .. BL-090). This ADR records the
cross-cutting decisions for implementing that backlog so the per-item
detail can live in code docstrings and the backlog rather than in many
small ADRs.

## Decision

### 1. Additive, not breaking

Every L2 change is additive to the L1 Protocols. New behaviour arrives
as new optional keyword parameters (default preserving L1 behaviour),
new modules, or new Protocols that sit beside the existing ones
(`MemoryStoreCAS` beside `MemoryStore`, never replacing it). No L1
import path or signature is removed. The `_example` workload and the
224 L1 tests keep passing unchanged; L2 only adds.

### 2. Optional backends are import-lazy

Production memory adapters (Redis, S3, DynamoDB) and the OTel sink
depend on third-party packages that must not become hard dependencies
of the core. Each adapter lives in its own module, imports its driver
lazily inside `__init__` (raising a clear `MemoryError` /
`ImportError`-derived message naming the extra to install), and is
exposed through a `[project.optional-dependencies]` extra. The package
imports and type-checks with none of them installed. Their test suites
skip cleanly when the driver or a live server is absent, so CI stays
green without external services. SQLite is the exception: it is stdlib,
so it is always available and always tested.

### 3. Validation surfaces at load time, not mid-run

BL-010/011/012 push configuration errors to the earliest point: the
workload loader rejects a manifest whose `name` does not match its
package directory and (when a `SkillRegistry` is supplied) a manifest
that references an unknown skill; a skill's `allowed-tools` is checked
against a harness `ToolCatalog`. The cost of a late failure (a tool
rejected mid-run, a wrong namespace bound) far exceeds a load-time
check.

### 4. New Protocols mirror existing shapes

`KeyProvider`, `MemoryStoreCAS`, `EmbeddingProvider`, `SkillSource`,
and `RecoveryHandler` follow the established conventions:
`@runtime_checkable` Protocol, async where the operation can do I/O,
frozen Pydantic models for data that crosses a boundary, frozen
dataclasses for in-process callables. Composition (BL-052/060) is the
ADR 0002 rule made concrete: predicate-set intersection,
governance/approval union.

### 5. Adapter integration follows the convergent vendor shape

The PydanticAI adapter (BL-001..004, 073) wires the L1 guard/budget
surface by wrapping each tool with a pre-execution guard check and by
consuming PydanticAI `RunUsage` into the `BudgetTracker`. A background
asyncio watchdog enforces wall-clock; per-tool quotas extend the single
counter; streaming accumulates usage. This is the ADR 0003 design with
the runtime wiring it deferred. PydanticAI 1.97 is pinned; the adapter
isolates its API behind the `Runtime` Protocol so churn stays local.

## Consequences

Positive: L1 stays stable; production deployments get durable backends,
real enforcement, observability, and composition without a rewrite.
Audit evidence now spans memory operations too.

Negative: more optional extras to document; the PydanticAI adapter
couples to a pre-1.0 library (mitigated by the Protocol boundary and a
pin). The backlog is large; it is delivered in reviewable batches on
one branch.

Neutral: a single ADR rather than ten. Per-item rationale lives in
module docstrings and `docs/backlog.md`, which is the durable record.

## Backlog mapping

`docs/backlog.md` is the line-item tracker; status moves
pending -> in-progress -> resolved there. This ADR is the why; the
backlog is the what and the state.

## Revisit triggers

- A third-party driver forces a hard dependency (revisit the lazy
  pattern).
- PydanticAI ships a breaking change the Protocol boundary cannot
  absorb (revisit ADR 0001/0003).
- Predicate-set intersection proves wrong for real skill+workload
  contract composition (revisit ADR 0002).
