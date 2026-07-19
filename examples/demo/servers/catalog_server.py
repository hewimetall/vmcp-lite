"""Toy read-only MCP server for the vmcp-lite demo."""

from __future__ import annotations

from fastmcp import FastMCP

mcp = FastMCP("demo-catalog")

COLORS = {
    "fox": "orange",
    "frog": "green",
    "whale": "blue",
}


@mcp.tool()
def lookup_color(animal: str) -> dict[str, str | bool]:
    """Return a color for a tiny built-in animal catalogue."""
    normalized = animal.strip().lower()
    color = COLORS.get(normalized)
    if color is None:
        return {
            "animal": normalized,
            "found": False,
            "color": "unknown",
        }
    return {
        "animal": normalized,
        "found": True,
        "color": color,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
