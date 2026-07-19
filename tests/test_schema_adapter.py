"""Rust schema adapter tests."""

from __future__ import annotations

import asyncio
import json
import queue
import time
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
        self.attached = False
        self.pending: queue.Queue[dict[str, object]] = queue.Queue()
        self.responses: dict[str, str] = {}
        self.next_request_id = 1

    def attach_call_bridge(self) -> None:
        self.attached = True

    def detach_call_bridge(self) -> None:
        self.attached = False

    def receive_tool_call(self, timeout_ms: int = 100) -> str | None:
        _ = timeout_ms
        try:
            request = self.pending.get(timeout=timeout_ms / 1000)
        except queue.Empty:
            return None
        return json.dumps(request)

    def respond_tool_call(self, request_id: str, result_json: str) -> bool:
        self.responses[request_id] = result_json
        return True

    def fail_tool_call(self, request_id: str, message: str) -> bool:
        self.responses[request_id] = json.dumps(
            {"isError": True, "text": message, "json": None}
        )
        return True

    def execute(self, query: str, variables_json: str | None = None) -> str:
        assert self.attached is True
        if "first:" in query and "second:" in query:
            first_id = self._enqueue("demo", "echo", {"value": 1}, "query")
            second_id = self._enqueue("demo", "echo", {"value": 2}, "query")
            return json.dumps(
                {
                    "data": {
                        "first": json.loads(_wait_for_fake_response(self, first_id)),
                        "second": json.loads(_wait_for_fake_response(self, second_id)),
                    },
                    "errors": [],
                }
            )

        _ = query
        arguments = json.loads(variables_json or "{}")
        request_id = self._enqueue("demo", "echo", arguments, "query")
        payload = json.loads(_wait_for_fake_response(self, request_id))
        return json.dumps({"data": {"demo_echo": payload}, "errors": []})

    def _enqueue(
        self,
        server: str,
        tool: str,
        arguments: dict[str, object],
        operation: str,
    ) -> str:
        request_id = f"fake-{self.next_request_id}"
        self.next_request_id += 1
        self.pending.put(
            {
                "request_id": request_id,
                "server": server,
                "tool": tool,
                "arguments": arguments,
                "operation": operation,
            }
        )
        return request_id


class UnusedUpstreams:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def caller_for(self, server_id: ServerId) -> object:
        raise AssertionError(f"Rust stub should not call upstreams yet: {server_id!r}")


def _wait_for_fake_response(engine: BridgeFakeRustEngine, request_id: str) -> str:
    deadline = time.monotonic() + 1
    while request_id not in engine.responses:
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out waiting for {request_id}")
        time.sleep(0.001)
    return engine.responses.pop(request_id)


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
            await engine.close_call_bridge()
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


def test_rust_schema_engine_drains_parallel_query_requests_without_serializing() -> None:
    class RecordingCaller:
        def __init__(self) -> None:
            self.in_flight = 0
            self.max_in_flight = 0

        async def call_tool(self, request: BridgeRequest) -> dict[str, object]:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                await asyncio.sleep(0.01)
                return {"value": request.arguments["value"]}
            finally:
                self.in_flight -= 1

    async def run() -> None:
        def engine_factory(catalogue_json: str) -> BridgeFakeRustEngine:
            return BridgeFakeRustEngine(catalogue_json)

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
            response = await asyncio.wait_for(
                engine.execute(
                    GraphQLRequest(
                        query="{ first: demo_echo { json } second: demo_echo { json } }"
                    ),
                    registry,
                    UnusedUpstreams(),
                ),
                timeout=1,
            )
        finally:
            await engine.close_call_bridge()
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

        assert response == GraphQLResponse(
            data={
                "first": {"isError": False, "text": '{"value":1}', "json": {"value": 1}},
                "second": {"isError": False, "text": '{"value":2}', "json": {"value": 2}},
            }
        )
        assert caller.max_in_flight > 1

    asyncio.run(run())
