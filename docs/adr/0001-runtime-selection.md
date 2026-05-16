# ADR 0001: Runtime adapter selection

- Status: Accepted
- Date: 2026-05-16
- Authors: rmednitzer

## Context

The `agents` repository hosts agentic workloads with declared boundaries between workloads, skills, harness, and memory. The harness must orchestrate workloads through an agent runtime that handles model I/O, tool registration, and conversation state. The choice of runtime affects type-safety guarantees, multi-provider portability, observability surface, and the depth of code we own versus delegate.

We evaluated:

- PydanticAI: typed I/O via Pydantic models, multi-provider, native MCP client, dependency injection, Logfire observability.
- Anthropic Claude Agent SDK: tight integration with Claude tool use, computer use, MCP, skills; vendor-locked.
- LangGraph: explicit state-machine orchestration, checkpointing, durable execution; heavy abstractions and large dependency surface.
- Smolagents (Hugging Face): code-execution-first ReAct agents with bundled sandboxing; immature ecosystem.
- OpenAI Agents SDK: provider-aligned, weaker typing than PydanticAI for the same shape of problem.
- Rolling our own thin adapter over `anthropic` and `openai` SDKs: maximum sovereignty, most code, no clear leverage gain over PydanticAI for a small public surface.

## Decision

Adopt PydanticAI as the default runtime adapter, accessed exclusively through the `harness.runtime.Runtime` Protocol.

Constraints:

1. Workloads depend on the `Runtime` Protocol, not on `pydantic_ai` directly. A workload that imports `pydantic_ai` is a contract violation.
2. The harness owns sandboxing, action budgets, tool-use authorization, and observability. PydanticAI provides I/O typing and provider abstraction only.
3. Multi-agent orchestration, durable execution, and stateful graphs, if needed in the future, are implemented in the harness, not delegated to PydanticAI.
4. The Protocol surface is intentionally minimal (`run`, `stream`). Additional runtime capabilities, when needed, are added to the Protocol and implemented across all adapters.

## Consequences

Positive:

- Pydantic-validated I/O at the model boundary aligns with the boundary-classify-enforce primitive.
- Multi-provider support (Anthropic, OpenAI, Gemini, Ollama, local llama.cpp via OpenAI-compat) preserves provider sovereignty.
- Native MCP client support means workloads talk to Vertex tools without a custom shim.
- Cognitive load is reduced because PydanticAI is already in production use elsewhere in the operator's stack.

Negative:

- PydanticAI is pre-1.0 software. Minor versions may introduce breaking changes. Pinning in `pyproject.toml` and version lockfiles in CI mitigate this.
- Multi-agent orchestration support in PydanticAI is thin. If a workload requires graph-based state machines, the harness must build that layer or a second adapter (e.g. LangGraph) must be added.
- Sandboxing is not provided by PydanticAI. This is intentional and consistent with the harness contract, but it is non-trivial work and must not be skipped.

Neutral:

- Adapter Protocol enables swapping runtimes without rewriting workloads. The cost of this option is a small abstraction overhead and a discipline of not bypassing the Protocol.

## Alternatives considered and rejected

- Claude Agent SDK as repo default: rejected for vendor lock-in and sovereignty constraint. May be used as a per-workload runtime adapter if a workload is Claude-fixed.
- LangGraph as repo default: rejected for heavy abstractions and dependency surface. May be added as a second adapter if state-machine orchestration becomes a primary pattern.
- Roll-your-own over `anthropic` + `openai`: rejected because PydanticAI's surface area is small enough that the leverage is better spent on the harness, memory, and contract layers above it.

## Revisit triggers

This decision is revisited if any of the following occur:

- PydanticAI introduces a breaking change that cannot be absorbed within one minor version cycle.
- A workload pattern emerges that the Protocol abstraction cannot accommodate without leaking implementation details.
- An alternative runtime delivers materially better support for a capability we depend on (e.g. durable execution, sandboxing).
