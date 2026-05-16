"""Tests for workloads.manifest."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.budgets import ActionBudget
from harness.mcp import MCPServerSpec, MCPTransport
from memory.types import Namespace
from workloads.manifest import RuntimeSpec, WorkloadManifest


def test_runtime_spec_minimal() -> None:
    spec = RuntimeSpec(adapter="pydantic-ai", model="anthropic:claude-opus-4-7")
    assert spec.adapter == "pydantic-ai"
    assert spec.model == "anthropic:claude-opus-4-7"
    assert spec.parameters == {}


def test_runtime_spec_with_parameters() -> None:
    spec = RuntimeSpec(
        adapter="pydantic-ai",
        model="ollama:qwen3:30b-a3b",
        parameters={"temperature": 0.2, "top_p": 0.95},
    )
    assert spec.parameters["temperature"] == 0.2


def test_runtime_spec_is_frozen() -> None:
    spec = RuntimeSpec(adapter="a", model="m")
    with pytest.raises(ValidationError):
        spec.adapter = "b"  # type: ignore[misc]


def test_manifest_minimal() -> None:
    m = WorkloadManifest(
        name="example",
        version="0.1.0",
        description="A workload.",
        runtime=RuntimeSpec(adapter="pydantic-ai", model="anthropic:claude-opus-4-7"),
    )
    assert m.name == "example"
    assert m.memory_namespace is None
    assert m.mcp_servers == []
    assert m.skills == []
    assert m.dispatcher is None
    assert m.budget is None
    assert m.exit_conditions == {}


def test_manifest_full() -> None:
    m = WorkloadManifest(
        name="example",
        version="0.1.0",
        description="A workload with all fields.",
        runtime=RuntimeSpec(adapter="pydantic-ai", model="m"),
        memory_namespace=Namespace(name="example-state", workload="example"),
        mcp_servers=[
            MCPServerSpec(
                name="local-tool",
                transport=MCPTransport.STDIO,
                command="/bin/echo",
            )
        ],
        skills=["search", "summarize"],
        dispatcher="keyword",
        budget=ActionBudget(max_steps=20, max_tokens=8000),
        exit_conditions={"on_first_match": True},
    )
    assert m.memory_namespace is not None
    assert m.memory_namespace.name == "example-state"
    assert len(m.mcp_servers) == 1
    assert m.skills == ["search", "summarize"]
    assert m.dispatcher == "keyword"
    assert m.budget is not None
    assert m.budget.max_steps == 20
    assert m.exit_conditions == {"on_first_match": True}


def test_manifest_is_frozen() -> None:
    m = WorkloadManifest(
        name="x",
        version="0.1.0",
        description="d",
        runtime=RuntimeSpec(adapter="a", model="m"),
    )
    with pytest.raises(ValidationError):
        m.name = "y"  # type: ignore[misc]


def test_manifest_requires_name() -> None:
    with pytest.raises(ValidationError):
        WorkloadManifest(  # type: ignore[call-arg]
            version="0.1.0",
            description="d",
            runtime=RuntimeSpec(adapter="a", model="m"),
        )


def test_manifest_requires_runtime() -> None:
    with pytest.raises(ValidationError):
        WorkloadManifest(  # type: ignore[call-arg]
            name="x",
            version="0.1.0",
            description="d",
        )


def test_manifest_round_trip_json() -> None:
    m = WorkloadManifest(
        name="r",
        version="0.1.0",
        description="d",
        runtime=RuntimeSpec(adapter="a", model="m"),
        skills=["s1"],
    )
    raw = m.model_dump_json()
    parsed = WorkloadManifest.model_validate_json(raw)
    assert parsed == m
