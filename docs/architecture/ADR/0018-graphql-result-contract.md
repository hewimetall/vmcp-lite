# ADR-0018: query_graphql result + error contract

- Status: Accepted
- Date: 2026-07-19
- Scope: `mcp_tools`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0018-graphql-result-contract>

## Context

Clients need one stable shape for success, validation failure, upstream tool
error, and engine error.

## Decision

`query_graphql` always returns a `CallToolResult` whose text content is a
GraphQL JSON object.

Engine or validation failure:

```json
{ "data": null, "errors": [{ "message": "..." }] }
```

Successful execution, including upstream field failures:

```json
{ "data": { "...": "..." }, "errors": [] }
```

Upstream tool failures are represented as field payloads such as
`ToolCallResult { isError, text, json }` inside GraphQL data. Serialize failures
become synthetic GraphQL errors. There is no envelope beyond GraphQL JSON.

## Consequences

- Agents parse one JSON shape.
- Field-level upstream errors do not need to fail the outer MCP tool call.
- The contract aligns with full vmcp `query_graphql` behavior.

## Related flows

- `graphql-validation-reject`
- `query-graphql-happy-path`
- `upstream-tool-error`
