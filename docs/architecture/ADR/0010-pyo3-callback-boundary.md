# ADR-0010: PyO3 SchemaEngine to ToolCaller callback boundary

- Status: Accepted
- Date: 2026-07-19
- Scope: `graphql_engine`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0010-pyo3-callback-boundary>

## Context

Rust owns GraphQL execution. Python owns MCP child-process I/O. The boundary
must stay hexagonal: Rust must not spawn upstream processes or import the MCP
SDK.

## Decision

`vmcp._graphql` exposes:

- `Engine.build(catalogue_json)`
- `Engine.execute(query, variables, call_tool)`

`call_tool` is the callback boundary into Python and is provided through a
port/bridge. The catalogue JSON is the single schema input.

## Consequences

- Tests can use fake `call_tool` implementations.
- Rust remains isolated from the MCP SDK.
- Python can bridge async pool calls without moving process-spawn ownership
  into Rust.

## Related flows

- `query-graphql-happy-path`
- `seq-parallel-query`
- `tdd-port-contract`
