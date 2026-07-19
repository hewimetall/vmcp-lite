"""FastMCP driving adapter for vmcp-lite."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from vmcp.adapters.composition.root import CompositionRoot, build_composition_root


def create_server(composition_root: CompositionRoot | None = None) -> FastMCP:
    """Create the FastMCP server exposing the single public query_graphql tool."""
    root = composition_root or build_composition_root()
    mcp = FastMCP("vmcp-lite")

    @mcp.tool()
    async def query_graphql(
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query against the virtual MCP gateway."""
        response = await root.execute_query_graphql.execute(query=query, variables=variables)
        return response.model_dump(mode="json", exclude_none=True)

    return mcp


mcp = create_server()

__all__ = ["create_server", "mcp"]
