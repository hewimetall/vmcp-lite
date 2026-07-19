# Architecture flows - vmcp-lite

Source of truth: <https://architecture.runmcp.ru/view/ws-vmcp-lite/flows>

## Key flows

| Flow | Kind | Summary |
|------|------|---------|
| [`boot-aggregation`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/boot-aggregation) | C4 dynamic | Host spawns vmcp-lite; `Boot` delegates to `CompositionRoot`, which loads registry data, spawns/list-tools on upstreams, builds the schema, starts the bridge, registers `query_graphql`, and reports ready. |
| [`composition-wiring`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/composition-wiring) | C4 dynamic | `CompositionRoot` is the only place that constructs concrete adapters and injects them into `BootAggregation` and `ExecuteGraphql`. |
| [`query-graphql-happy-path`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/query-graphql-happy-path) | C4 dynamic | MCP `tools/call` enters the FastMCP adapter, delegates to `ExecuteGraphql`, uses the `SchemaEngine` and `ToolCaller` ports, calls child stdio upstreams, and returns a GraphQL JSON result. |
| [`discovery-ladder`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/discovery-ladder) | C4 dynamic | Agents discover capabilities inside GraphQL: `servers`, `search(q)`, `__type(name)`, then one batched query or mutation. |
| [`parallel-query-fanout`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/parallel-query-fanout) | C4 dynamic | Read-only Query aliases validate once, execute concurrently across upstreams, and merge into one GraphQL response. |
| [`sequential-mutation`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/sequential-mutation) | C4 dynamic | Write-capable Mutation fields execute in order; per-upstream locks still apply. |
| [`async-bridge-call`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/async-bridge-call) | Sequence | Rust resolvers submit `CallRequest` messages without holding the GIL; a Python asyncio worker calls the upstream pool and returns `ToolResult` over a oneshot response. |
| [`graceful-shutdown`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/graceful-shutdown) | C4 dynamic | On stdin EOF or host kill, `CompositionRoot.shutdown` cancels the bridge, closes all upstream sessions, terminates children, and reaps them. |

## Supporting flows

| Flow | Kind | Summary |
|------|------|---------|
| [`config-load-env-override`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/config-load-env-override) | C4 dynamic | Config loader reads `vmcp.toml`, merges nested `VMCP_*` environment overrides, and returns settings plus registry DTOs for boot. |
| [`graphql-validation-reject`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/graphql-validation-reject) | C4 dynamic | Empty or oversized GraphQL documents fail pre-validation and return an error before any upstream call. |
| [`partial-spawn-failure`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/partial-spawn-failure) | C4 dynamic | Boot keeps the successful upstreams, records failed spawns, builds the schema from survivors, and initializes in degraded mode. |
| [`seq-parallel-query`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/seq-parallel-query) | Sequence | Sequence view of two Query aliases running in parallel through different upstreams. |
| [`seq-sequential-mutation`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/seq-sequential-mutation) | Sequence | Sequence view of Mutation field1 completing before field2 starts. |
| [`sidecar-readonly-override`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/sidecar-readonly-override) | C4 dynamic | Sidecar specs override upstream `readOnlyHint` data before the schema builder assigns tools to Query or Mutation. |
| [`state-mcp-session`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/state-mcp-session) | State | Lifecycle state machine: process start, booting, ready or degraded, serving, shutdown, children reaped. |
| [`state-readonly-bucketing`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/state-readonly-bucketing) | State | Tool state machine: raw tool, sidecar merge, read/write bucket, Query or Mutation namespace, parallel or serial execution. |
| [`tdd-port-contract`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/tdd-port-contract) | C4 dynamic | Tests are written first against use cases and fake ports; real PyO3 and upstream-pool adapters follow behind port contracts. |
| [`upstream-tool-error`](https://architecture.runmcp.ru/view/ws-vmcp-lite/flows/upstream-tool-error) | C4 dynamic | Upstream `isError` becomes `ToolResult.is_error` inside GraphQL data; the outer `query_graphql` call can still return a success envelope. |

## Execution invariants

- `Query` is for read-only tools; independent aliases across servers may run
  concurrently.
- `Mutation` is for writes and unknown read-only status; fields run
  sequentially.
- A single upstream child remains single-flight behind `call_lock`.
- Bad queries and failed upstream tools are represented as structured results;
  they do not crash the stdio host.
- Boot and shutdown are owned by the composition root.
