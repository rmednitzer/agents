# ADR 0002: Behavioral contracts as the workload-harness boundary

- Status: Accepted
- Date: 2026-05-16
- Authors: rmednitzer

## Context

Workloads in this repo run against a Runtime adapter (see ADR 0001). The harness needs an enforceable contract at the workload-runtime boundary so that input/output type discipline, invariants over execution, and policy on tool actions are not delegated to prompt-level pleading.

The design space was surveyed against Anthropic's Claude Agent SDK (hooks: PreToolUse, PostToolUse, Stop), OpenAI's Agents SDK (input/output/tool guardrails with tripwires, needs_approval, interruptions, resumable state), and Google's ADK (callbacks at agent/model/tool boundaries, App-level Plugins). All three converged on roughly the same pattern: typed predicates at well-defined points, halt-on-violation via exception or tripwire, structured events for observability. The Bhardwaj 2026 Agent Behavioral Contracts paper (arXiv:2602.22302) formalizes the same idea as a four-tuple (P, I, G, R) with (p, delta, k)-satisfaction.

## Decision

Adopt a four-category behavioral contract attached to each workload:

- Preconditions: predicates over input state, validated before the runtime call.
- Invariants: predicates over observable state, validated during the run.
- Postconditions: predicates over output state, validated after the runtime call.
- Governance: predicates over individual proposed actions. The type surface is in this phase; live wiring to the runtime lands in Phase 2.

Each predicate carries a stable name and a Severity:

- HARD violations halt the run via a subtype of HarnessError.
- SOFT violations emit a Violation event to the configured EventSink and the run continues.

A Contract is a frozen dataclass parameterized by the input and output Pydantic models. A Predicate is a Protocol with name, severity, and __call__. The `predicate` decorator wraps a function into a FunctionPredicate that conforms to the Protocol.

Human-in-the-loop is supported via an interruption pattern (ApprovalInterruption + ResumableState) modeled on the OpenAI Agents SDK. In Phase 1 these are data types and unit-tested transition logic; Phase 2 wires them into the runtime so that proposed tool calls actually pause and produce a ResumableState.

Structured events carry OTel-compatible identifiers (trace_id, span_id, parent_span_id) so that an OTel sink can be added in a future phase without touching event producers. Sinks ship: NullSink (default), MemorySink (tests), JsonlSink (local dev and audit packs), MultiSink (fan-out).

## Consequences

Positive:

- The workload-runtime boundary is explicit and enforceable. The harness owns enforcement; workloads declare expectations.
- The contract object is portable: it does not depend on any agent framework. Swapping PydanticAI for another runtime adapter does not change the contract.
- Audit evidence is a side-effect of normal operation: every violation emits a structured event with a stable name, severity, timestamp, and trace identifier. This serves NIS2, AI Act, ISO 27001 / 42001, and DORA audit needs without an additional logging layer.
- The Hard / Soft split matches operational reality: most production violations are soft (a quality predicate failed, log it) rather than hard (the contract was broken, halt).

Negative:

- Governance enforcement and live approval-firing require runtime-side hooks. Phase 1 declares the surface; Phase 2 wires it. Until then, governance predicates and `approval_required` are non-functional.
- A workload that wants to enforce invariants over mid-run state needs to pass observable state through the harness; the default in Phase 1 checks invariants against the input only.

Neutral:

- The contract is intentionally a dataclass rather than a Pydantic model. Pydantic ser/de is not useful for callables (predicate functions). Events, manifests, and ResumableState remain Pydantic because they cross process boundaries.

## Alternatives considered and rejected

- Using OpenAI's guardrail surface verbatim. Rejected because it couples the contract surface to a vendor SDK.
- Implementing the Bhardwaj AgentAssert library. Rejected because the paper is patent-pending and licensed CC BY-NC-ND 4.0; mimicking the specific runtime apparatus is legally risky. We adopt the general pattern (which predates the paper, going back to Meyer 1992 Design-by-Contract) and cite the paper as related work in the framework comments.
- A single "guardrail" abstraction without the Hard/Soft distinction. Rejected because audit evidence work (governance, regulatory compliance) requires the soft category to be a first-class structural element rather than a logging convention.
- Storing predicates as named references resolved against a registry. Rejected for L1 as unnecessary indirection; revisit if predicate sharing across contracts becomes a pattern.

## Deferred to L2

- Live governance enforcement: requires runtime hooks. Phase 2.
- Live approval interruption: requires runtime hooks. Phase 2.
- Recovery handlers for soft violations (the R in the Bhardwaj tuple). L2.
- JSD distributional drift instrumentation. L2.
- OTel-Collector-compatible sink. L2 (event surface is already OTel-ready).
- Composition of multiple contracts (workload contract + skill contract). L2 (intersection of predicate sets).

## Revisit triggers

This decision is revisited if:

- The Hard / Soft binary turns out to be insufficient and we find ourselves wanting more severity gradations.
- The dataclass-not-Pydantic choice for Contract becomes painful (e.g. if we want to serialize contracts to share across services).
- A workload pattern emerges where invariants cannot be expressed as state predicates and require richer abstractions (temporal logic, history-dependent predicates).
