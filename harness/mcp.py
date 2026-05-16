"""MCP server lifecycle types for the harness.

MCPServerSpec is declared in a WorkloadManifest (Phase 4) and passed to
the runtime via the Runtime Protocol. MCPLifecycle is the adapter contract
for starting, stopping, and introspecting MCP servers; the default
PydanticAIRuntime implements it via PydanticAI's own MCP integration
(implementation deferred to first workload).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "MCPHandle",
    "MCPLifecycle",
    "MCPServerSpec",
    "MCPTransport",
    "ToolSpec",
]


class MCPTransport(StrEnum):
    """MCP transport mode."""

    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class MCPServerSpec(BaseModel):
    """Declarative MCP server specification.

    Validated at construction:

    - stdio transport requires command.
    - http and sse transports require url.
    - timeout_seconds must be positive.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    transport: MCPTransport
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 30.0
    allowed_tools: list[str] | None = None

    @model_validator(mode="after")
    def _check_transport_fields(self) -> MCPServerSpec:
        if self.transport == MCPTransport.STDIO and not self.command:
            raise ValueError("stdio transport requires command")
        if self.transport in (MCPTransport.HTTP, MCPTransport.SSE) and not self.url:
            raise ValueError(f"{self.transport.value} transport requires url")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        return self


class ToolSpec(BaseModel):
    """Minimal description of a tool exposed by an MCP server or runtime."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class MCPHandle:
    """Opaque handle returned by MCPLifecycle.start.

    Contents are adapter-specific; the harness treats this as an opaque
    token for stop() and list_tools() calls.
    """

    server_name: str
    adapter_handle: Any = None


@runtime_checkable
class MCPLifecycle(Protocol):
    """Adapter-implemented MCP server lifecycle.

    Implementations:

    - Start an MCP server per spec (subprocess for stdio, HTTP/SSE client
      for the network transports).
    - Stop and clean up on stop().
    - Return the server's tool catalog on list_tools().

    The Runtime adapter typically holds a single MCPLifecycle instance and
    manages handles internally; workloads do not see this surface.
    """

    async def start(self, spec: MCPServerSpec) -> MCPHandle: ...

    async def stop(self, handle: MCPHandle) -> None: ...

    async def list_tools(self, handle: MCPHandle) -> list[ToolSpec]: ...
