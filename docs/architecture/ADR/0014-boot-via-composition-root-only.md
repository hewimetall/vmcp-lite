# ADR-0014: Boot only through CompositionRoot

- Status: Accepted
- Date: 2026-07-19
- Scope: `wiring`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0014-boot-via-composition-root-only>

## Context

Earlier flow sketches had boot code talking directly to pool, schema, and
config adapters. That is a god-object smell and violates the hexagonal boundary.

## Decision

The public entrypoint calls only `CompositionRoot.wire()` and `run()`.

- `CompositionRoot` constructs concrete adapters, injects ports into use cases,
  and registers the FastMCP tool.
- `BootAggregation` orchestrates registry load, upstream catalogue resolution,
  and schema build through ports only.
- FastMCP boot helpers are thin aliases that delegate to the composition root.
- Only the composition root may reference concrete adapter constructors.

## Consequences

- Tests can wire the system with fakes.
- Boot flow aligns with the composition-wiring model.
- Direct boot-to-adapter edges are removed from the mental model.

## Related flows

- `boot-aggregation`
- `composition-wiring`
- `graceful-shutdown`
