# ADR-0019: Default read_only when hint and sidecar absent

- Status: Accepted
- Date: 2026-07-19
- Scope: `schema_build`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0019-readonly-default>

## Context

Upstream MCP tools often omit `readOnlyHint`. Treating unknown tools as reads
would allow accidental parallel writes through GraphQL Query.

## Decision

When both upstream annotations and sidecar specs omit read-only metadata,
default `read_only=false`.

That means unknown tools land under GraphQL `Mutation`, where execution is
serial and explicitly write-capable. Sidecars should set `read_only=true` for
known safe tools. Discovery exposes read-only status so agents can see the
bucket.

## Consequences

- Unknown tools are safer by default.
- Some read-only tools require sidecar metadata to move into Query.
- The default avoids surprise side effects under Query parallelism.

## Related flows

- `sidecar-readonly-override`
- `state-readonly-bucketing`
