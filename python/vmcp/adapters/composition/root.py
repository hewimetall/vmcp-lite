"""Composition root for vmcp-lite adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vmcp.adapters.driven.registry import SidecarRegistryLoader
from vmcp.domain.models import (
    GraphQLError,
    GraphQLRequest,
    GraphQLResponse,
    ServerId,
    ToolRegistry,
)
from vmcp.domain.ports import RegistryLoader, SchemaEngine, ToolCaller, UpstreamPool
from vmcp.domain.usecases import ExecuteQueryGraphQL, compose_usecases


class EmptyRegistryLoader:
    """Registry loader stub until stdio discovery is implemented."""

    async def load_registry(self) -> ToolRegistry:
        # TODO(ADR-0006): implement discovery ladder and registry parsing.
        return ToolRegistry()


class StubUpstreamPool:
    """Upstream pool stub; CallBridge-backed implementation is owned elsewhere."""

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def caller_for(self, server_id: ServerId) -> ToolCaller:
        raise NotImplementedError(f"upstream pool is not wired for {server_id.value!r}")


class StubSchemaEngine:
    """Schema engine stub until the Rust/PyO3 boundary is wired."""

    async def execute(
        self,
        request: GraphQLRequest,
        registry: ToolRegistry,
        upstreams: UpstreamPool,
    ) -> GraphQLResponse:
        _ = (request, upstreams)
        return GraphQLResponse(
            errors=(
                GraphQLError(
                    message="query_graphql schema engine is not wired yet",
                    extensions={
                        "code": "schema_engine_not_wired",
                        "registry_tool_count": len(registry.tools),
                    },
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class CompositionRoot:
    """Container for vmcp-lite dependencies."""

    execute_query_graphql: ExecuteQueryGraphQL
    configured: bool = False


def build_composition_root(
    *,
    config_path: str | Path | None = None,
    registry_loader: RegistryLoader | None = None,
    upstreams: UpstreamPool | None = None,
    schema_engine: SchemaEngine | None = None,
) -> CompositionRoot:
    """Build the vmcp-lite composition root from injected ports or stubs."""
    resolved_registry_loader = registry_loader or (
        SidecarRegistryLoader.from_config_file(config_path)
        if config_path is not None
        else EmptyRegistryLoader()
    )
    resolved_upstreams = upstreams or StubUpstreamPool()
    resolved_schema_engine = schema_engine or StubSchemaEngine()

    return CompositionRoot(
        execute_query_graphql=compose_usecases(
            registry_loader=resolved_registry_loader,
            upstreams=resolved_upstreams,
            schema_engine=resolved_schema_engine,
        ),
        configured=all(
            (
                registry_loader is not None or config_path is not None,
                upstreams is not None,
                schema_engine is not None,
            )
        ),
    )


__all__ = [
    "CompositionRoot",
    "EmptyRegistryLoader",
    "StubSchemaEngine",
    "StubUpstreamPool",
    "build_composition_root",
]
