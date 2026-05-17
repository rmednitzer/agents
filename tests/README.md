# tests/

Test suite. Mirrors the source layout: `tests/agents/`, `tests/workloads/`, `tests/skills/`, `tests/harness/`, `tests/memory/`, `tests/evaluation/`.

Tests are not optional for harness and memory; advisory for workloads and skills. The `evaluation` component (the BL-130 behavioural gate) is itself tested and counted in the coverage gate.

External memory adapters are exercised with in-process doubles
(`fakeredis`, `moto`) and `cryptography` / `opentelemetry-sdk`; these
ship in the `dev` extra so `uv sync --all-extras` runs everything in CI.
Tests `pytest.importorskip` their driver, so a partial environment
skips cleanly rather than failing. The PydanticAI adapter is tested
deterministically with `TestModel` / `FunctionModel` (no network or API
keys).
