# ADR-0000: MUST: SOLID + DRY + TDD + HEX

- Status: Accepted
- Date: 2026-07-19
- Scope: `vmcp_lite`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0000-must-solid-dry-tdd-hex>

## Context

vmcp-lite is a greenfield rewrite of a reduced vmcp. Without hard process
constraints, the implementation can drift into a FastMCP/PyO3 blob with
duplicated registry and GraphQL logic and weak adapter tests.

## Decision

All code and architecture must follow these constraints:

1. HEX: domain/use cases and port contracts live inward; FastMCP, upstream
   stdio, config, and registry adapters sit outside. Dependencies point inward.
2. SOLID: modules have single responsibilities, extension happens through
   ports/adapters, and call sites depend on contracts rather than SDK/PyO3
   concrete types.
3. DRY: one owner per concern, including schema build, registry parse, tool
   calls, and shared wire formats.
4. TDD: behavior is specified by failing tests first. Coverage gates are
   Python >=98 percent and Rust llvm-cov >=93 percent per crate.

Any ADR or PR that violates these constraints must be rejected or superseded.

## Consequences

- Reviews are judged against ports/adapters boundaries.
- Edge adapters can change without rewriting the domain.
- More modules are expected than a one-file server.
- Coverage CI is mandatory before acceptance.

## Related flows

- `query-graphql-happy-path`
