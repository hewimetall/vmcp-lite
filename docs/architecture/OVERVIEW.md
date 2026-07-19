# Architecture overview - vmcp-lite

Source of truth: architect-c4 workspace
[`ws-vmcp-lite`](https://architecture.runmcp.ru/view/ws-vmcp-lite).

Useful viewer links:

| View | Link |
|------|------|
| Context | <https://architecture.runmcp.ru/view/ws-vmcp-lite?layer=context> |
| Containers | <https://architecture.runmcp.ru/view/ws-vmcp-lite?layer=container&parent=vmcp_lite> |
| Flows | <https://architecture.runmcp.ru/view/ws-vmcp-lite/flows> |
| ADRs | <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs> |

```text
MCP host / agent
      |
      | MCP over stdio
      v
+----------------------------------------------------------+
| vmcp-lite                                                |
|                                                          |
|  FastMCP stdio  ->  Domain/use cases + ports             |
|       |                 |                                |
|       |                 +-> Rust/PyO3 GraphQL kernel     |
|       |                 +-> Registry/config adapter      |
|       |                 +-> Upstream stdio pool          |
+----------------------------------------------------------+
      |                         |
      | reads                   | spawns + tools/call
      v                         v
vmcp.toml / registry.json   Upstream MCP servers
specs/*.json                (child stdio processes)
```

## System context

`vmcp-lite` is a stdio-only virtual MCP gateway. It deliberately keeps the
GraphQL aggregation idea from full `vmcp`, but drops HTTP, OAuth, admin, task,
proxy, recorder, Docker/deploy, and skills surfaces.

| Actor / system | Role |
|----------------|------|
| MCP host / agent | Cursor, Claude Desktop, or any stdio MCP client. |
| Operator | Edits `vmcp.toml`, `registry.json`, and `specs/*.json`; deploys through `mcp.json`. |
| Config files on disk | Boot-time registry and sidecar data. |
| Upstream MCP servers | Child stdio servers spawned by vmcp-lite. |
| vmcp (full) | Reference product; vmcp-lite inherits aggregation, not the full transport/admin surface. |

## Containers

| Container | Technology | Responsibility |
|-----------|------------|----------------|
| Driving: FastMCP stdio | Python / FastMCP | Thin MCP adapter: exposes `query_graphql`, maps MCP `tools/call` to use cases, and owns no GraphQL domain rules. |
| Domain + Ports | Python protocols / Rust traits | Inward HEX core: use cases and contracts for `ToolCaller`, `ToolCatalogue`, `RegistrySource`, and `SchemaEngine`. |
| Adapter: GraphQL schema kernel | Rust / PyO3 / maturin | Implements `SchemaEngine`; owns schema build, discovery roots, validation, Query/Mutation partitioning, and execution scheduling. |
| Driven: Registry + config | TOML / JSON / Pydantic | Loads `vmcp.toml`, `registry.json`, sidecar specs, and `VMCP_*` overrides. |
| Driven: Upstream pool | Python / MCP SDK | Spawns child stdio MCP servers, lists tools, applies sidecars, and calls tools with per-upstream locking. |

## Key components

| Area | Components |
|------|------------|
| FastMCP stdio | Boot / orchestration, MCP tools, Composition root |
| Domain + Ports | Use cases, Ports, Domain models |
| GraphQL kernel | Schema builder, JSON Schema to GQL, Query executor, Async call bridge |
| Registry + config | Config loader |
| Upstream pool | Pool runtime, Stdio child session |

## Runtime rules

- Boot goes through `CompositionRoot` only: wire adapters, inject ports, run
  `BootAggregation`, build the schema, start the bridge, then register
  `query_graphql`.
- The public MCP surface is exactly one tool:
  `query_graphql(query, variables?, operation_name?)`.
- Discovery happens inside GraphQL: `servers`, `search(q)`, `__type(name)`,
  then one batched query or mutation.
- Read-only tools are placed under GraphQL `Query` and can fan out in parallel
  across upstream servers.
- Write-capable or unknown tools are placed under GraphQL `Mutation` and execute
  sequentially.
- Same-upstream calls are serialized by that upstream session's `call_lock`,
  even when Query aliases are otherwise parallel.
- Upstream spawn failures degrade the pool rather than killing the stdio host.
- Shutdown cancels the bridge, closes the pool, and reaps child processes.

ADRs: [`ADR/`](ADR/README.md)  
Flows: [`FLOWS.md`](FLOWS.md)
