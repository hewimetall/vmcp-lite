"""Use case tests for the query_graphql boundary."""

from __future__ import annotations

import asyncio

from vmcp.domain.models import (
    GraphQLRequest,
    GraphQLResponse,
    ServerId,
    ToolCall,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)
from vmcp.domain.ports import ToolCaller
from vmcp.domain.usecases import ExecuteQueryGraphQL


class RecordingRegistryLoader:
    def __init__(self) -> None:
        self.loaded = False
        self.registry = ToolRegistry(
            tools=(
                ToolDefinition(
                    server_id=ServerId(value="demo"),
                    name="echo",
                    input_schema={"type": "object"},
                ),
            ),
        )

    async def load_registry(self) -> ToolRegistry:
        self.loaded = True
        return self.registry


class UnusedToolCaller:
    async def call_tool(self, call: ToolCall) -> ToolResult:
        raise AssertionError(f"schema engine stub should not call tools directly: {call!r}")


class RecordingUpstreamPool:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def caller_for(self, server_id: ServerId) -> ToolCaller:
        _ = server_id
        return UnusedToolCaller()


class RecordingSchemaEngine:
    def __init__(self) -> None:
        self.request: GraphQLRequest | None = None
        self.registry: ToolRegistry | None = None
        self.upstreams: RecordingUpstreamPool | None = None

    async def execute(
        self,
        request: GraphQLRequest,
        registry: ToolRegistry,
        upstreams: RecordingUpstreamPool,
    ) -> GraphQLResponse:
        self.request = request
        self.registry = registry
        self.upstreams = upstreams
        return GraphQLResponse(data={"answer": 42})


def test_execute_query_graphql_loads_registry_and_delegates_to_schema_engine() -> None:
    async def run() -> None:
        registry_loader = RecordingRegistryLoader()
        upstreams = RecordingUpstreamPool()
        schema_engine = RecordingSchemaEngine()
        usecase = ExecuteQueryGraphQL(
            registry_loader=registry_loader,
            upstreams=upstreams,
            schema_engine=schema_engine,
        )

        response = await usecase.execute(
            query="query Echo($text: String!) { demo_echo(text: $text) }",
            variables={"text": "hello"},
        )

        assert response == GraphQLResponse(data={"answer": 42})
        assert registry_loader.loaded is True
        assert schema_engine.request == GraphQLRequest(
            query="query Echo($text: String!) { demo_echo(text: $text) }",
            variables={"text": "hello"},
        )
        assert schema_engine.registry == registry_loader.registry
        assert schema_engine.upstreams is upstreams
        assert upstreams.started is False
        assert upstreams.stopped is False

    asyncio.run(run())


def test_execute_query_graphql_defaults_variables_to_empty_mapping() -> None:
    async def run() -> None:
        registry_loader = RecordingRegistryLoader()
        upstreams = RecordingUpstreamPool()
        schema_engine = RecordingSchemaEngine()
        usecase = ExecuteQueryGraphQL(
            registry_loader=registry_loader,
            upstreams=upstreams,
            schema_engine=schema_engine,
        )

        await usecase.execute(query="{ __typename }")

        assert schema_engine.request == GraphQLRequest(query="{ __typename }")

    asyncio.run(run())

