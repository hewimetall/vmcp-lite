# ADR-0006: Sole MCP tool: query_graphql + discovery ladder

- Status: Accepted
- Date: 2026-07-19
- Scope: `mcp_tools`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0006-sole-tool-query-graphql>

## Context

Full vmcp exposes additional task, proxy, skill, and prompt surfaces. vmcp-lite
keeps the agent contract intentionally small.

## Decision

Advertise exactly one MCP tool:

```text
query_graphql(query, variables?, operation_name?)
```

Discovery is performed inside GraphQL:

1. `{ servers }`
2. `{ search(q) }`
3. `__type(name)`
4. One batched query or mutation

There are no per-upstream MCP tools. Server instructions teach batching and the
discovery ladder.

## Consequences

- Agents learn one tool.
- Introspection stays lazy.
- Skills/prompts YAML remain deferred.

## Related flows

- `discovery-ladder`
- `parallel-query-fanout`
- `query-graphql-happy-path`
