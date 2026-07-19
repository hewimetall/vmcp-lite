# ADR-0001: Stdio-only ingress

- Status: Accepted
- Date: 2026-07-19
- Scope: `vmcp_lite`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0001-stdio-only>

## Context

Full vmcp exposes HTTP gateway, OAuth, admin UI, Docker/Caddy, and mcpb
surfaces. vmcp-lite is for local MCP hosts that communicate over stdin/stdout.

## Decision

vmcp-lite exposes MCP exclusively over stdio. There is no Axum listener, no
`/mcp` HTTP endpoint, no OAuth/JWKS, no admin SPA, and no streamable-HTTP
ingress. The pipe is the trust boundary.

## Consequences

- Local single-client MCP hosts work directly.
- Multi-tenant remote gateway behavior is out of scope.
- Auth utilities and deployment surfaces are removed.
- Docs and packaging focus on uv/maturin plus `mcp.json`.

## Related flows

- `query-graphql-happy-path`
