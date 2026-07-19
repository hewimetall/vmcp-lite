# ADR-0012: Domain ownership: Python use cases + Rust schema kernel

- Status: Accepted
- Date: 2026-07-19
- Scope: `domain`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0012-domain-ownership>

## Context

ADR-0002 left ownership between Python protocols and Rust traits ambiguous.
Without a clear split, vmcp-lite risks duplicating bucketing and validation
rules or letting FastMCP own GraphQL semantics.

## Decision

Python domain owns orchestration:

- Ports: `ToolCaller`, `ToolCatalogue`, `RegistrySource`, `SchemaEngine`.
- Use cases: `BootAggregation`, `ExecuteGraphql`.
- Models: registry, upstream specs, sidecar specs, resolved tools, tool results,
  and schema limits.

Rust schema kernel owns GraphQL semantics:

- JSON Schema to GraphQL argument mapping.
- Dynamic schema build and Query/Mutation partitioning.
- Discovery roots: `servers`, `search`.
- Pre-validation, depth/complexity/response limits.
- Execute and fan-out scheduling.

Rules that must not be duplicated in Python: partition logic, pre-validation,
and discovery ranking.

## Consequences

- Orchestration remains Python.
- GraphQL behavior has a single owner in Rust.
- Python tests can wire fake `SchemaEngine`; Rust tests cover partitioning and
  validation.

## Related flows

- `composition-wiring`
- `tdd-port-contract`
