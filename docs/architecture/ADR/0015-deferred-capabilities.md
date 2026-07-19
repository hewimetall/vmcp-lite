# ADR-0015: Explicitly deferred: hot-reload, HTTP upstream, skills YAML

- Status: Accepted
- Date: 2026-07-19
- Scope: `vmcp_lite`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0015-deferred-capabilities>

## Context

vmcp-lite is intentionally smaller than full vmcp. Drift handling, remote MCP,
skills, and task workflows are useful but out of v1 scope unless explicitly
reintroduced.

## Decision

Out of scope for v1:

1. `tools.lock.json` and hot schema swap on upstream `tools/list_changed`.
2. HTTP or streamable-HTTP upstream transport.
3. Operator skills YAML to MCP prompts.
4. `run_task`, SEP-1686, and SQLite tasks.
5. OAuth, admin, recorder, notify bus, mcpb, and Docker deploy.

In scope for v1:

- Boot-time schema only.
- Stdio upstreams.
- GraphQL discovery through `servers`, `search`, and `__type`.
- Restart to pick up registry or tool changes.

## Consequences

- The product boundary is explicit.
- Deferred features can return through future ADRs and new adapters.
- Docs must not describe lite as a drop-in full-vmcp replacement.

## Related flows

- `boot-aggregation`
- `discovery-ladder`
