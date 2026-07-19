# ADR-0017: Lifecycle: initialize ready + graceful child reap

- Status: Accepted
- Date: 2026-07-19
- Scope: `wiring`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0017-lifecycle-shutdown>

## Context

vmcp-lite must not leave orphaned `npx`, `uv`, or binary child upstreams when
the MCP host closes stdin or kills the process.

## Decision

- Boot is complete only after composition is wired, `BootAggregation` finishes,
  partial pool failures are handled, and FastMCP listens on stdio.
- On stdin EOF, SIGTERM, or FastMCP shutdown hook, call
  `CompositionRoot.shutdown()`.
- Shutdown cancels the bridge task, closes all upstream sessions, terminates
  child processes, waits, and escalates to kill on timeout.
- The engine and tokio runtime are dropped during shutdown.
- Long-running tasks must be joinable or explicitly cancelled.

## Consequences

- No zombie upstream processes.
- The MCP session state flow includes shutdown and reaping.
- Tests should spawn a mock upstream, shut down, and assert its PID is gone.

## Related flows

- `composition-wiring`
- `graceful-shutdown`
- `partial-spawn-failure`
- `state-mcp-session`
