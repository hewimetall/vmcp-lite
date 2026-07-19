# ADR-0011: Async bridge: tokio GraphQL to asyncio ToolCaller

- Status: Accepted
- Date: 2026-07-19
- Scope: `graphql_engine`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0011-async-bridge-pyo3>

## Context

`async-graphql` runs on a Rust-owned tokio runtime inside `vmcp._graphql`.
`UpstreamPool.call_tool` is Python asyncio. A naive callback can deadlock the
GIL, nest event loops, or destroy Query parallelism.

## Decision

- The Rust engine owns a dedicated multi-thread tokio runtime.
- `Engine.execute` must not block the Python asyncio loop thread.
- Rust resolvers communicate with Python through a channel-based bridge:
  `CallRequest` messages go to a Python asyncio worker, and `CallResponse`
  returns over a oneshot channel.
- Rust awaits responses without holding the GIL.
- Multiple Query aliases can be in flight; the pool applies per-upstream locks.

Forbidden:

- `asyncio.run` inside Rust callbacks.
- Scheduling onto a non-running event loop.
- Holding the GIL across `tools/call` awaits.

## Consequences

- More wiring code is required.
- Runtime ownership stays explicit.
- Cross-upstream parallelism is preserved.
- Tests must cover parallel aliases and deadlock prevention.

## Related flows

- `async-bridge-call`
- `parallel-query-fanout`
- `query-graphql-happy-path`
- `seq-parallel-query`
- `tdd-port-contract`
