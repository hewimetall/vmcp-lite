# ADR-0016: Multi-instance stdio: one process per host client

- Status: Accepted
- Date: 2026-07-19
- Scope: `vmcp_lite`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0016-multi-instance-stdio>

## Context

Each Cursor or Claude Desktop `mcp.json` entry spawns its own vmcp-lite process.
That implies duplicated upstream child processes if several host clients use
the same registry.

## Decision

The intended v1 model is:

- One MCP host session equals one vmcp-lite process.
- One vmcp-lite process owns one private `UpstreamPool`.
- There is no shared daemon or multiplexed gateway in lite.
- Operators should keep registries slim and avoid duplicating heavy upstreams.
- A shared long-lived vmcp gateway remains the domain of full HTTP vmcp.

An optional `VMCP_UPSTREAM_REUSE` mode is rejected for v1.

## Consequences

- Isolation is predictable.
- Resource cost scales with the number of clients.
- The tradeoff is acceptable for local stdio.

## Related flows

- `boot-aggregation`
- `state-mcp-session`
