"""WorkloadManifest schema.

A workload bundle declares its identity, runtime requirements, memory
binding, MCP servers, skills, dispatcher choice, action budget, and
exit conditions in a manifest.yaml file. The loader (workloads.loader)
parses this into a WorkloadManifest Pydantic model.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from harness.budgets import ActionBudget
from harness.mcp import MCPServerSpec
from memory.types import Namespace

__all__ = [
    "RuntimeSpec",
    "WorkloadManifest",
]


class RuntimeSpec(BaseModel):
    """Runtime adapter selection for a workload.

    Attributes:
        adapter: Adapter name. The default adapter is "pydantic-ai";
            test bundles can use "in-process-stub" or similar.
        model: Model identifier as expected by the adapter. For
            pydantic-ai, this follows the "provider:model" convention,
            e.g. "anthropic:claude-opus-4-7", "openai:gpt-4o",
            "ollama:qwen3:30b-a3b". For stub adapters, "none" or any
            convention.
        parameters: Adapter-specific parameters (temperature, max tokens,
            top_p, etc.). Stored in the manifest. The harness does not
            apply these automatically; workload wiring code may interpret
            and forward them to the adapter.
    """

    model_config = ConfigDict(frozen=True)

    adapter: str
    model: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class WorkloadManifest(BaseModel):
    """Top-level manifest for a workload bundle.

    A workload directory contains:

    - manifest.yaml: this file, validated into WorkloadManifest
    - __init__.py: makes the directory a Python package
    - contract.py: must export `contract: Contract[InputT, OutputT]`
    - __main__.py: optional, must export `main: async callable`
    - README.md: optional, human-readable description

    Attributes:
        name: Workload identifier. Must match the package directory name.
        version: Semantic version of the workload.
        description: One-paragraph human description.
        runtime: Which runtime adapter and model to use.
        memory_namespace: Namespace this workload binds to, if any.
        mcp_servers: MCP servers the runtime starts for this workload.
        skills: Skill names the workload requires (resolved by
            SkillRegistry in Phase 5).
        dispatcher: Dispatcher name or skill name (resolved in Phase 5).
            None means no dispatch, the workload runs without skill
            routing.
        budget: Action budget for runs of this workload. None means
            unlimited.
        exit_conditions: Workload-specific exit criteria (e.g.
            {"on_first_match": true}). Interpretation is workload-
            defined.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    description: str
    runtime: RuntimeSpec
    memory_namespace: Namespace | None = None
    mcp_servers: list[MCPServerSpec] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    dispatcher: str | None = None
    budget: ActionBudget | None = None
    exit_conditions: dict[str, Any] = Field(default_factory=dict)
