# ADR-0007: Aggregation mode from GraphQL operation kind

- Status: Accepted
- Date: 2026-07-19
- Scope: `graphql_engine`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0007-query-parallel-mutation-serial>

## Context

vmcp-lite needs deterministic fan-out without adding a separate aggregation
mode flag. Full vmcp already buckets tools by read-only metadata.

## Decision

- Tools with `read_only=true` are exposed under GraphQL `Query` and execute
  aliased fields in parallel.
- Tools with `read_only=false` are exposed under GraphQL `Mutation` and execute
  fields sequentially.
- Sidecar metadata may override upstream read-only hints.
- Per-upstream `call_lock` still serializes calls to the same child process.

## Consequences

- Aggregation mode is structural and follows GraphQL expectations.
- Agents must use Query for reads and Mutation for writes.
- The same-server serialization rule must be documented.

## Related flows

- `parallel-query-fanout`
- `sequential-mutation`
- `seq-parallel-query`
- `seq-sequential-mutation`
- `state-readonly-bucketing`
- `sidecar-readonly-override`
