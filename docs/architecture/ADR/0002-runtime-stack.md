# ADR-0002: Runtime stack: Python FastMCP + Rust/PyO3

- Status: Accepted
- Date: 2026-07-19
- Scope: `vmcp_lite`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0002-runtime-stack>

## Context

Full vmcp is a pure Rust workspace. vmcp-lite intentionally follows the
mcp-presentation style: Python for the MCP adapter and orchestration, Rust for
the performance-sensitive GraphQL schema kernel.

## Decision

- Python 3.14 and FastMCP provide the hexagonal driving adapter for stdio MCP.
- Rust via PyO3/maturin provides the driven `SchemaEngine` adapter.
- Domain/use cases and port contracts live inward.
- The upstream pool is a driven adapter implementing `ToolCaller` and
  `ToolCatalogue`.
- Packaging uses uv plus maturin.

## Consequences

- Development requires Rust stable and maturin.
- FastMCP is pinned at or above 3.4.x.
- GraphQL resolvers call a `ToolCaller` port and do not spawn processes.
- A pure-Rust rmcp monolith is not the vmcp-lite direction.

## Related flows

- `query-graphql-happy-path`
