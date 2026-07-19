"""FastMCP driving adapter tests."""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Client

from vmcp.adapters.driving.fastmcp_server import create_server


def test_fastmcp_server_exposes_only_query_graphql_tool() -> None:
    async def run() -> None:
        async with Client(create_server()) as client:
            tools = await client.list_tools()

        assert [tool.name for tool in tools] == ["query_graphql"]

    asyncio.run(run())


def test_query_graphql_tool_uses_composition_root_stub() -> None:
    async def run() -> dict[str, Any]:
        async with Client(create_server()) as client:
            result = await client.call_tool(
                "query_graphql",
                {"query": "{ __typename }"},
            )

        data = result.data
        assert isinstance(data, dict)
        return data

    data = asyncio.run(run())

    assert data["errors"][0]["extensions"]["code"] == "schema_engine_not_wired"

