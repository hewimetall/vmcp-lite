"""Registry loader tests for ADR-0008 sidecar format."""

from __future__ import annotations

import asyncio
from pathlib import Path

from vmcp.adapters.composition.root import build_composition_root
from vmcp.adapters.driven.registry import SidecarRegistryLoader
from vmcp.domain.models import (
    GraphQLRequest,
    GraphQLResponse,
    ServerId,
    ToolCall,
    ToolRegistry,
    ToolResult,
)
from vmcp.domain.ports import ToolCaller, UpstreamPool

FIXTURES = Path(__file__).parent / "fixtures" / "registry"


def test_sidecar_registry_loader_loads_yaml_registry_and_json_sidecar() -> None:
    async def run() -> None:
        loader = SidecarRegistryLoader.from_config_file(FIXTURES / "vmcp.toml")

        registry = await loader.load_registry()

        assert loader.validation_issues == ()
        assert [upstream.name for upstream in loader.upstreams] == ["alpha"]
        assert loader.upstreams[0].command == "python3"
        assert loader.upstreams[0].args == ("-m", "alpha_server")
        assert loader.upstreams[0].env == {"ALPHA_MODE": "test"}
        assert [(tool.server_id.value, tool.name, tool.read_only) for tool in registry.tools] == [
            ("alpha", "search", True),
            ("alpha", "write_note", False),
        ]
        assert registry.tools[0].description == "Search alpha data"
        assert registry.tools[0].input_schema["properties"]["query"]["type"] == "string"

    asyncio.run(run())


def test_invalid_registry_entries_are_skipped_with_diagnostics() -> None:
    async def run() -> None:
        loader = SidecarRegistryLoader(
            FIXTURES / "partial-invalid.json",
            spec_dir=FIXTURES / "specs",
        )

        registry = await loader.load_registry()

        assert [(tool.server_id.value, tool.name, tool.read_only) for tool in registry.tools] == [
            ("ok", "echo", True),
            ("bad-sidecar", "kept", False),
        ]
        assert [upstream.name for upstream in loader.upstreams] == [
            "ok",
            "bad-sidecar",
            "missing-sidecar",
        ]

        diagnostics = [f"{issue.location}: {issue.message}" for issue in loader.validation_issues]
        assert any("HTTP upstreams are not supported" in diagnostic for diagnostic in diagnostics)
        assert any("upstreams[2].command" in diagnostic for diagnostic in diagnostics)
        assert any("upstreams[3]: must be an object" in diagnostic for diagnostic in diagnostics)
        assert any("must not be blank" in diagnostic for diagnostic in diagnostics)
        assert any(
            "does-not-exist.json" in diagnostic and "not found" in diagnostic
            for diagnostic in diagnostics
        )

    asyncio.run(run())


class CapturingSchemaEngine:
    def __init__(self) -> None:
        self.registry_tool_count: int | None = None

    async def execute(
        self,
        request: GraphQLRequest,
        registry: ToolRegistry,
        upstreams: UpstreamPool,
    ) -> GraphQLResponse:
        _ = (request, upstreams)
        self.registry_tool_count = len(registry.tools)
        return GraphQLResponse(data={"registry_tool_count": self.registry_tool_count})


class UnusedToolCaller:
    async def call_tool(self, call: ToolCall) -> ToolResult:
        raise AssertionError(f"composition registry test should not call tools: {call!r}")


class NoopUpstreamPool:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def caller_for(self, server_id: ServerId) -> ToolCaller:
        _ = server_id
        return UnusedToolCaller()


def test_composition_root_uses_registry_loader_when_config_path_is_provided() -> None:
    async def run() -> None:
        schema_engine = CapturingSchemaEngine()
        root = build_composition_root(
            config_path=FIXTURES / "vmcp.toml",
            upstreams=NoopUpstreamPool(),
            schema_engine=schema_engine,
        )

        response = await root.execute_query_graphql.execute(query="{ __typename }")

        assert root.configured is True
        assert response == GraphQLResponse(data={"registry_tool_count": 2})
        assert schema_engine.registry_tool_count == 2

    asyncio.run(run())
