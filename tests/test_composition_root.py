"""Composition root boot wiring tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from vmcp.adapters.composition.root import boot_composition_root
from vmcp.adapters.driven.upstream import StdioUpstreamConfig
from vmcp.domain.models import ServerId, ToolCall, ToolResult
from vmcp.domain.ports import ToolCaller

FIXTURES = Path(__file__).parent / "fixtures" / "registry"


class FakeRustEngine:
    def __init__(self, catalogue_json: str) -> None:
        self.catalogue = json.loads(catalogue_json)

    def execute(self, query: str, variables_json: str | None = None) -> str:
        _ = (query, variables_json)
        return json.dumps({"data": {"toolCount": len(self.catalogue)}, "errors": []})


class UnusedToolCaller:
    async def call_tool(self, call: ToolCall) -> ToolResult:
        raise AssertionError(f"Rust SchemaEngine stub should not call tools yet: {call!r}")


class FakeUpstreamPool:
    def __init__(self, configs: Sequence[StdioUpstreamConfig]) -> None:
        self.configs = tuple(configs)
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def caller_for(self, server_id: ServerId) -> ToolCaller:
        _ = server_id
        return UnusedToolCaller()


def test_boot_composition_root_wires_registry_schema_pool_and_shutdown() -> None:
    async def run() -> None:
        pools: list[FakeUpstreamPool] = []
        catalogues: list[list[dict[str, object]]] = []

        def upstream_pool_factory(configs: Sequence[StdioUpstreamConfig]) -> FakeUpstreamPool:
            pool = FakeUpstreamPool(configs)
            pools.append(pool)
            return pool

        def rust_engine_factory(catalogue_json: str) -> FakeRustEngine:
            catalogues.append(json.loads(catalogue_json))
            return FakeRustEngine(catalogue_json)

        root = await boot_composition_root(
            config_path=FIXTURES / "vmcp.toml",
            upstream_pool_factory=upstream_pool_factory,
            rust_engine_factory=rust_engine_factory,
        )
        try:
            response = await root.execute_query_graphql.execute("{ __typename }")
        finally:
            await root.stop()

        assert root.configured is True
        assert response.data == {"toolCount": 2}
        assert [config.server_id.value for config in pools[0].configs] == ["alpha"]
        assert pools[0].configs[0].command == "python3"
        assert pools[0].configs[0].args == ("-m", "alpha_server")
        assert pools[0].configs[0].env == {"ALPHA_MODE": "test"}
        assert pools[0].started is True
        assert pools[0].stopped is True
        assert [(tool["server"], tool["name"]) for tool in catalogues[0]] == [
            ("alpha", "search"),
            ("alpha", "write_note"),
        ]
        assert catalogues[0][0]["server_description"] == "Alpha demo tools"
        assert root.call_bridge_task is not None
        assert root.call_bridge_task.done() is True

    asyncio.run(run())
