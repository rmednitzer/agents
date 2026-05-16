"""Scaffold smoke tests.

Verifies that core packages import and Runtime Protocol is exposed.
"""

from __future__ import annotations

import harness
from harness.runtime import PydanticAIRuntime, Runtime


def test_harness_imports() -> None:
    """The harness package is importable."""
    assert harness is not None


def test_runtime_protocol_exposed() -> None:
    """Runtime Protocol is exported from harness."""
    assert Runtime is not None


def test_pydantic_ai_runtime_constructs() -> None:
    """PydanticAIRuntime constructs with a model string."""
    runtime = PydanticAIRuntime(model="anthropic:claude-opus-4-7")
    assert runtime.name == "pydantic-ai"
    assert runtime.model == "anthropic:claude-opus-4-7"
