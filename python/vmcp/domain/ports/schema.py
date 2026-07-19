"""Port for the Rust-owned GraphQL schema engine boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from vmcp.domain.models import GraphQLRequest, GraphQLResponse, ToolRegistry
from vmcp.domain.ports.upstream import UpstreamPool


@runtime_checkable
class SchemaEngine(Protocol):
    """Executes virtual GraphQL queries without exposing Rust semantics to Python."""

    async def execute(
        self,
        request: GraphQLRequest,
        registry: ToolRegistry,
        upstreams: UpstreamPool,
    ) -> GraphQLResponse:
        """Execute a query against a registry-backed virtual GraphQL schema."""
        ...

