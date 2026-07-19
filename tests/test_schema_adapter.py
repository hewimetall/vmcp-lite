"""Rust schema adapter tests."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress

from vmcp.adapters.bridge.call_bridge import BridgeRequest, CallBridge
from vmcp.adapters.driven.schema import RustSchemaEngine, build_tool_catalogue_json
from vmcp.domain.models import (
    GraphQLRequest,
    GraphQLResponse,
    ServerId,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)


class FakeRustEngine:
    def __init__(self, catalogue_json: str) -> None:
        self.catalogue = json.loads(catalogue_json)
        self.calls: list[tuple[str, str | None]] = []

    def execute(self, query: str, variables_json: str | None = None) -> str:
        self.calls.append((query, variables_json))
        return json.dumps(
            {
                "data": {
                    "toolCount": len(self.catalogue),
                    "variables": json.loads(variables_json or "{}"),
                },
                "errors": [],
            }
        )


class BridgeFakeRustEngine:
    def __init__(self, catalogue_json: str) -> None:
        self.catalogue = json.loads(catalogue_json)
        self.call_tool = None

    def set_tool_caller(self, call_tool) -> None:
        self.call_tool = call_tool

    def execute(self, query: str, variables_json: str | None = None) -> str:
        _ = query
        assert self.call_tool is not None
        payload = json.loads(
            self.call_tool("demo", "echo", variables_json or "{}", "query")
        )
        return json.dumps({"data": {"demo_echo": payload}, "errors": []})


class UnusedUpstreams:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def caller_for(self, server_id: ServerId) -> object:
        raise AssertionError(f"Rust stub should not call upstreams yet: {server_id!r}")


def test_build_tool_catalogue_json_serializes_domain_registry() -> None:
    registry = ToolRegistry(
        tools=(
            ToolDefinition(
                server_id=ServerId(value="demo"),
                name="echo",
                description="Echo text",
                input_schema={"type": "object"},
                read_only=True,
            ),
        )
    )

    catalogue = json.loads(
        build_tool_catalogue_json(
            registry,
            upstream_descriptions={"demo": "Demo tools"},
        )
    )

    assert catalogue == [
        {
            "description": "Echo text",
            "input_schema": {"type": "object"},
            "name": "echo",
            "read_only": True,
            "server": "demo",
            "server_description": "Demo tools",
        }
    ]


def test_rust_schema_engine_wraps_extension_contract() -> None:
    async def run() -> None:
        engines: list[FakeRustEngine] = []

        def engine_factory(catalogue_json: str) -> FakeRustEngine:
            engine = FakeRustEngine(catalogue_json)
            engines.append(engine)
            return engine

        registry = ToolRegistry(
            tools=(
                ToolDefinition(
                    server_id=ServerId(value="demo"),
                    name="echo",
                    input_schema={"type": "object"},
                ),
            )
        )
        engine = RustSchemaEngine.from_registry(registry, engine_factory=engine_factory)

        response = await engine.execute(
            GraphQLRequest(query="{ demo_echo }", variables={"text": "hi"}),
            registry,
            UnusedUpstreams(),
        )

        assert response == GraphQLResponse(data={"toolCount": 1, "variables": {"text": "hi"}})
        assert engines[0].catalogue[0]["server"] == "demo"
        assert engines[0].calls == [("{ demo_echo }", '{"text":"hi"}')]

    asyncio.run(run())


def test_rust_schema_engine_attached_call_bridge_invokes_tool_caller() -> None:
    class RecordingCaller:
        def __init__(self) -> None:
            self.requests: list[BridgeRequest] = []

        async def call_tool(self, request: BridgeRequest) -> ToolResult:
            self.requests.append(request)
            return ToolResult(
                server_id=ServerId(value=request.server),
                tool_name=request.tool,
                content={"echo": request.arguments["text"]},
            )

    async def run() -> None:
        engines: list[BridgeFakeRustEngine] = []

        def engine_factory(catalogue_json: str) -> BridgeFakeRustEngine:
            engine = BridgeFakeRustEngine(catalogue_json)
            engines.append(engine)
            return engine

        registry = ToolRegistry(
            tools=(
                ToolDefinition(
                    server_id=ServerId(value="demo"),
                    name="echo",
                    input_schema={"type": "object"},
                    read_only=True,
                ),
            )
        )
        bridge = CallBridge()
        caller = RecordingCaller()
        worker = asyncio.create_task(bridge.serve(caller))
        engine = RustSchemaEngine.from_registry(registry, engine_factory=engine_factory)
        engine.set_call_bridge(bridge)

        try:
            response = await engine.execute(
                GraphQLRequest(query="{ demo_echo }", variables={"text": "hi"}),
                registry,
                UnusedUpstreams(),
            )
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

        assert response == GraphQLResponse(
            data={
                "demo_echo": {
                    "isError": False,
                    "text": '{"echo":"hi"}',
                    "json": {"echo": "hi"},
                }
            }
        )
        assert len(caller.requests) == 1
        assert caller.requests[0].server == "demo"
        assert caller.requests[0].tool == "echo"
        assert caller.requests[0].arguments == {"text": "hi"}

    asyncio.run(run())
