# ADR-0003: Strip surface inherited from full vmcp

- Status: Accepted
- Date: 2026-07-19
- Scope: `vmcp_lite`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0003-strip-full-vmcp>

## Context

vmcp-lite is a truncated rewrite. Full vmcp contains tasks, SQLite, proxy
endpoints, skills YAML, session recorder, OpenTelemetry, HTTP upstreams,
benchmarks, deployment, mcpb, and other surfaces that are not needed for a
stdio aggregator.

## Decision

Keep:

- `registry.json` for stdio upstreams.
- Sidecar `read_only` metadata.
- Dynamic GraphQL and `query_graphql`.
- GraphQL `servers`, `search`, and type discovery.
- Depth and complexity limits.

Drop:

- `run_task` and task queues.
- `/mcp-proxy`.
- Skills/prompts YAML.
- Recorder, OAuth/tokens, admin, Docker/deploy/Caddy, mcpb, benchmarks.
- HTTP upstream transport.
- Notify bus as a first-class feature.

## Consequences

- The MCP tool surface shrinks to `query_graphql`.
- Documentation can stay small: architecture, ADRs, and quickstart.
- HTTP upstreams can return later only through a separate ADR.
