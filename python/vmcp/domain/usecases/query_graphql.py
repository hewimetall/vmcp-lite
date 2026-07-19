"""Use case for the public query_graphql MCP tool."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from vmcp.domain.models import GraphQLRequest, GraphQLResponse, JsonValue
from vmcp.domain.ports import RegistryLoader, SchemaEngine, UpstreamPool


@dataclass(frozen=True, slots=True)
class ExecuteQueryGraphQL:
    """Coordinate query_graphql without coupling to driven adapters."""

    registry_loader: RegistryLoader
    upstreams: UpstreamPool
    schema_engine: SchemaEngine

    async def execute(
        self,
        query: str,
        variables: Mapping[str, JsonValue] | None = None,
    ) -> GraphQLResponse:
        """Execute a GraphQL query through the schema-engine boundary."""
        request = GraphQLRequest(query=query, variables=dict(variables or {}))
        registry = await self.registry_loader.load_registry()
        return await self.schema_engine.execute(request, registry, self.upstreams)


def build_query_graphql_usecase(
    *,
    registry_loader: RegistryLoader,
    upstreams: UpstreamPool,
    schema_engine: SchemaEngine,
) -> ExecuteQueryGraphQL:
    """Compose the query_graphql use case from domain ports."""
    return ExecuteQueryGraphQL(
        registry_loader=registry_loader,
        upstreams=upstreams,
        schema_engine=schema_engine,
    )

