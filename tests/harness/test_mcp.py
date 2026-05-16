"""Tests for harness.mcp."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.mcp import (
    MCPHandle,
    MCPServerSpec,
    MCPTransport,
    ToolSpec,
)


def test_stdio_requires_command() -> None:
    with pytest.raises(ValidationError):
        MCPServerSpec(name="x", transport=MCPTransport.STDIO)


def test_http_requires_url() -> None:
    with pytest.raises(ValidationError):
        MCPServerSpec(name="x", transport=MCPTransport.HTTP)


def test_sse_requires_url() -> None:
    with pytest.raises(ValidationError):
        MCPServerSpec(name="x", transport=MCPTransport.SSE)


def test_stdio_spec_valid() -> None:
    spec = MCPServerSpec(
        name="local-tool",
        transport=MCPTransport.STDIO,
        command="/usr/bin/uvx",
        args=["--from", "some-pkg", "some-tool"],
    )
    assert spec.transport == MCPTransport.STDIO
    assert spec.command == "/usr/bin/uvx"


def test_http_spec_valid() -> None:
    spec = MCPServerSpec(
        name="remote-tool",
        transport=MCPTransport.HTTP,
        url="https://mcp.example.com/v1",
        headers={"Authorization": "Bearer ..."},
        timeout_seconds=60.0,
    )
    assert spec.transport == MCPTransport.HTTP
    assert spec.url == "https://mcp.example.com/v1"
    assert spec.timeout_seconds == 60.0


def test_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        MCPServerSpec(
            name="x",
            transport=MCPTransport.STDIO,
            command="/bin/x",
            timeout_seconds=0,
        )


def test_spec_is_frozen() -> None:
    spec = MCPServerSpec(
        name="x",
        transport=MCPTransport.STDIO,
        command="/bin/x",
    )
    with pytest.raises(ValidationError):
        spec.name = "y"  # type: ignore[misc]


def test_transport_values() -> None:
    assert MCPTransport.STDIO == "stdio"
    assert MCPTransport.HTTP == "http"
    assert MCPTransport.SSE == "sse"


def test_tool_spec_minimal() -> None:
    tool = ToolSpec(name="search", description="search the web")
    assert tool.name == "search"
    assert tool.input_schema == {}


def test_mcp_handle_holds_opaque_data() -> None:
    handle = MCPHandle(server_name="s1", adapter_handle={"pid": 12345})
    assert handle.server_name == "s1"
    assert handle.adapter_handle == {"pid": 12345}


def test_allowed_tools_defaults_none() -> None:
    spec = MCPServerSpec(
        name="x",
        transport=MCPTransport.STDIO,
        command="/bin/x",
    )
    assert spec.allowed_tools is None
