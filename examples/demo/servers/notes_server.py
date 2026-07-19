"""Toy mutable MCP server for the vmcp-lite demo."""

from __future__ import annotations

from fastmcp import FastMCP

mcp = FastMCP("demo-notes")

NOTES: list[str] = []


@mcp.tool()
def remember_note(text: str) -> dict[str, object]:
    """Append a note to this process' in-memory list."""
    note = text.strip()
    if not note:
        return {
            "accepted": False,
            "count": len(NOTES),
            "notes": list(NOTES),
        }

    NOTES.append(note)
    return {
        "accepted": True,
        "count": len(NOTES),
        "notes": list(NOTES),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
