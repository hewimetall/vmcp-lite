# ADR-0013: Parallelism scope + per-upstream call_lock

- Status: Accepted
- Date: 2026-07-19
- Scope: `upstream_pool`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0013-parallelism-and-call-lock>

## Context

Agents may assume all Query aliases run in parallel. Stdio MCP children are
usually single-flight, so same-server calls need a lock.

## Decision

- Cross-upstream Query aliases run in parallel.
- Same-upstream Query aliases are queued behind `UpstreamSession.call_lock`.
- Mutation fields are serial at the GraphQL layer; `call_lock` still applies.
- Server instructions must state that aliases to different servers run
  concurrently, while aliases to the same server are queued.
- Prefer batching reads across servers; for one server, prefer one tool that
  batches.

## Consequences

- The performance model is honest.
- Documentation does not overpromise parallelism.

## Related flows

- `async-bridge-call`
- `parallel-query-fanout`
- `seq-parallel-query`
- `sequential-mutation`
