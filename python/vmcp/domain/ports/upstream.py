"""Ports for calling stdio upstream MCP servers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from vmcp.domain.models import ServerId, ToolCall, ToolResult


@runtime_checkable
class ToolCaller(Protocol):
    """Calls tools on upstream MCP servers."""

    async def call_tool(self, call: ToolCall) -> ToolResult:
        """Execute one upstream tool call."""
        ...


@runtime_checkable
class UpstreamPool(Protocol):
    """Owns access to upstream MCP server clients."""

    async def start(self) -> None:
        """Start upstream resources, if any."""
        ...

    async def stop(self) -> None:
        """Stop upstream resources, if any."""
        ...

    def caller_for(self, server_id: ServerId) -> ToolCaller:
        """Return a tool caller for a discovered upstream server."""
        ...

