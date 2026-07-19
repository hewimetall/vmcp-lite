"""Contract tests for vmcp-lite domain ports."""

from __future__ import annotations

from vmcp.domain.models import (
    GraphQLRequest,
    GraphQLResponse,
    ServerId,
    ToolCall,
    ToolRegistry,
    ToolResult,
)
from vmcp.domain.ports import RegistryLoader, SchemaEngine, ToolCaller, UpstreamPool


class FakeToolCaller:
    async def call_tool(self, call: ToolCall) -> ToolResult:
        return ToolResult(
            server_id=call.server_id,
            tool_name=call.tool_name,
            content={"called": True},
        )


class FakeUpstreamPool:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def caller_for(self, server_id: ServerId) -> ToolCaller:
        _ = server_id
        return FakeToolCaller()


class FakeRegistryLoader:
    async def load_registry(self) -> ToolRegistry:
        return ToolRegistry()


class FakeSchemaEngine:
    async def execute(
        self,
        request: GraphQLRequest,
        registry: ToolRegistry,
        upstreams: UpstreamPool,
    ) -> GraphQLResponse:
        _ = (request, registry, upstreams)
        return GraphQLResponse(data={"ok": True})


def test_port_protocols_accept_structural_fakes() -> None:
    assert isinstance(FakeToolCaller(), ToolCaller)
    assert isinstance(FakeUpstreamPool(), UpstreamPool)
    assert isinstance(FakeRegistryLoader(), RegistryLoader)
    assert isinstance(FakeSchemaEngine(), SchemaEngine)

