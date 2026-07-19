# ADR-0004: Package layout: single vmcp + internal HEX

- Status: Accepted
- Date: 2026-07-19
- Scope: `domain`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0004-package-layout-open>

## Context

ADR-0000 forbids a flat script dump. vmcp-lite needs complete solution
architecture while avoiding a multi-package version matrix.

## Decision

Use one installable package with internal hexagonal boundaries:

```text
python/vmcp/
  domain/          # ports, use cases, models
  adapters/
    driving/       # FastMCP stdio
    driven/
      graphql/     # thin wrapper around vmcp._graphql
      upstream/    # MCP SDK pool
      config/      # TOML/JSON Pydantic
  wiring.py        # CompositionRoot
src/lib.rs         # PyO3 module vmcp._graphql
```

maturin builds `_graphql` into the same package. A flat FastMCP module without
domain boundaries remains forbidden.

## Consequences

- `uv sync` and `maturin develop` remain enough for local development.
- Tests can target clear port contracts and adapters.
- The package can split later without changing the inward ports.

## Related flows

- `boot-aggregation`
- `tdd-port-contract`
