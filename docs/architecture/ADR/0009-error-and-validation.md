# ADR-0009: Validation + error model

- Status: Accepted
- Date: 2026-07-19
- Scope: `executor`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0009-error-and-validation>

## Context

Agents need predictable failures. A bad query, failed spawn, or upstream tool
error must not crash the stdio host process.

## Decision

1. Pre-validation rejects empty documents and documents larger than 64 KiB.
2. `SchemaLimits` enforce max depth, complexity, and response bytes.
3. Upstream spawn failures are collected; the pool can boot in degraded mode.
4. Upstream `tools/call` errors become `ToolResult.is_error` inside GraphQL
   data, except for engine failures that become GraphQL errors.
5. The FastMCP process never exits because of one bad query.

## Consequences

- The host remains available after validation and upstream errors.
- Tests cover validation reject, partial spawn, and tool-error paths.

## Related flows

- `graphql-validation-reject`
- `partial-spawn-failure`
