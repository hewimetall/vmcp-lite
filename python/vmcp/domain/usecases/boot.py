"""Domain-level boot helpers for vmcp-lite use cases."""

from __future__ import annotations

from vmcp.domain.ports import RegistryLoader, SchemaEngine, UpstreamPool
from vmcp.domain.usecases.query_graphql import (
    ExecuteQueryGraphQL,
    build_query_graphql_usecase,
)


def compose_usecases(
    *,
    registry_loader: RegistryLoader,
    upstreams: UpstreamPool,
    schema_engine: SchemaEngine,
) -> ExecuteQueryGraphQL:
    """Build the currently supported vmcp-lite use case graph."""
    return build_query_graphql_usecase(
        registry_loader=registry_loader,
        upstreams=upstreams,
        schema_engine=schema_engine,
    )

