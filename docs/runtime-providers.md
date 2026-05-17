# Runtime providers

How a workload reaches the Anthropic or OpenAI API.

Short answer: it does not call a provider SDK directly. The harness delegates
all model I/O to PydanticAI, and PydanticAI delegates authentication to the
vendor SDK's conventional environment variables. No code in this repository
reads an API key.

## The boundary

[ADR 0001](./adr/0001-runtime-selection.md) makes this a hard contract:

- A workload depends on the `Runtime` Protocol (the `Runtime` class in
  `harness/runtime.py`), never on `pydantic_ai` or a vendor SDK. A workload
  that imports `pydantic_ai` is a contract violation. (Symbol references are
  used below rather than line numbers, which drift with every edit.)
- The harness owns sandboxing, action budgets, tool authorization, and
  observability. PydanticAI provides typed I/O and the provider abstraction
  only.
- Swapping Anthropic for OpenAI, Ollama, or a local OpenAI compatible
  endpoint is a one line manifest change, not a workload rewrite.

## Selecting a provider

A workload declares its runtime in `manifest.yaml`, validated into
`RuntimeSpec` (`workloads/manifest.py`):

```yaml
runtime:
  adapter: pydantic-ai
  model: anthropic:claude-opus-4-7
  parameters:
    temperature: 0.7
```

`model` follows PydanticAI's `provider:model` convention. The provider prefix
selects the API:

- `anthropic:claude-opus-4-7` reaches the Anthropic Messages API.
- `openai:gpt-4o` reaches the OpenAI API.
- `ollama:qwen3:30b-a3b` reaches a local Ollama server.

`parameters` (temperature, max tokens, top_p) is a manifest-level field on
`RuntimeSpec`. The harness does not auto-forward it: the default
`PydanticAIRuntime.__init__` takes `model`, `output_type`, `instructions`,
and the opt-in L3 `retry_policy` and `soft_reject_as_error` (ADR 0010), and
no harness code reads `RuntimeSpec.parameters`. A workload's own wiring code
is responsible for reading these values from its manifest and applying them
when it constructs the runtime or model. Declaring `parameters` alone
therefore has no effect today. For stub or test bundles the convention is
`adapter: in-process-stub` with `model: none`, which performs no model call.

## The connection path

1. The loader parses `manifest.yaml` into a `WorkloadManifest` carrying the
   `RuntimeSpec`.
2. The runtime is constructed with that model string:
   `PydanticAIRuntime(model="anthropic:claude-opus-4-7")`
   (`PydanticAIRuntime.__init__`).
3. `PydanticAIRuntime._build_agent` passes the string straight into
   PydanticAI's `Agent`:
   `Agent(self.model, output_type=..., instructions=..., tools=..., toolsets=...)`.
4. PydanticAI parses the provider prefix and instantiates the matching
   provider client. `pydantic-ai` (declared in `pyproject.toml`, the
   `[project] dependencies`) is the only model dependency; it brings the
   Anthropic and OpenAI client libraries transitively.
5. The vendor client reads its credentials from the process environment and
   makes the HTTPS call.

## Credentials

The connection is authenticated entirely by the underlying SDK reading its
conventional environment. A search across `agents`, `harness`, `memory`,
`workloads`, and `skills` for `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`api_key`, `base_url`, `os.environ`, and `getenv` returns nothing: credential
handling is delegated, by design.

Each provider prefix reads its own variables (verified against
`pydantic_ai` 1.97.0):

- Anthropic (`anthropic:`): key `ANTHROPIC_API_KEY`, endpoint override
  `ANTHROPIC_BASE_URL`.
- OpenAI (`openai:`): key `OPENAI_API_KEY`, endpoint override
  `OPENAI_BASE_URL`.
- Ollama (`ollama:`): `OLLAMA_BASE_URL` is required and PydanticAI raises a
  `UserError` if it is unset (`pydantic_ai/providers/ollama.py`);
  `OLLAMA_API_KEY` is optional and falls back to a placeholder. These are
  distinct from the `OPENAI_*` variables: setting `OPENAI_BASE_URL` does not
  configure an `ollama:` model.
- Other OpenAI compatible servers (llama.cpp, or Ollama addressed through
  its OpenAI compatible endpoint) reached via the `openai:` prefix:
  `OPENAI_API_KEY` (often a placeholder) with `OPENAI_BASE_URL` set to the
  local endpoint.

For a hosted provider (Anthropic, OpenAI), connecting reduces to two steps:
set the key variable in the runtime environment, and put `provider:model`
in the manifest. A local or self-hosted provider additionally requires its
endpoint variable: an `ollama:` model does not connect unless
`OLLAMA_BASE_URL` is set. Use an endpoint override to route a hosted
provider through a gateway or proxy.

PydanticAI is the layer that maps the `provider:` prefix to a client and
reads these variables. Its [models and providers
documentation](https://ai.pydantic.dev/models/overview/) lists the full
provider matrix and the exact key variable each provider expects, and the
[installation guide](https://ai.pydantic.dev/install/) covers
the optional per-provider extras.

## Constructing the runtime in code

The `agents` CLI dispatches deterministically and is model free on purpose,
so it runs without API keys (`agents.cli._model_free_dispatcher`; it now honours a model-free `keyword`/`embedding` manifest dispatcher, else falls back to keyword). An LLM backed
run is wired programmatically: a workload entry point builds a `Runtime` and
hands it to `run_under_contract`, which wraps it with the guard, budget, and
observability layers. The runtime accepts a provider string for live use or a
model instance for tests:

```python
from harness.runtime import PydanticAIRuntime

runtime = PydanticAIRuntime(model="anthropic:claude-opus-4-7")
# runtime.name == "pydantic-ai"
# pass to harness.enforcement.run_under_contract(runtime=runtime, ...)
```

## Testing without a provider

`PydanticAIRuntime.model` accepts a model instance as well as a string, so
tests pass `TestModel()` or `FunctionModel()` in place of the provider string
(the `PydanticAIRuntime` class docstring). These run deterministically with no
network and no API key, which is how the runtime adapter is exercised in CI.

## Current state

No live model workload ships yet. The only workload is the in process
`_example` stub (`model: none`, no model call). A real reference workload that
exercises the wired runtime against a live model, gated to skip without API
keys, is tracked as `BL-120` in [backlog.md](./backlog.md).

## See also

- [ADR 0001: Runtime adapter selection](./adr/0001-runtime-selection.md)
- [harness/README.md](../harness/README.md)
- `workloads/manifest.py` (the `RuntimeSpec` schema)
- [PydanticAI: installation and setup](https://ai.pydantic.dev/install/)
- [PydanticAI: models and providers](https://ai.pydantic.dev/models/overview/) (the `provider:model` matrix and per-provider API key variables)
