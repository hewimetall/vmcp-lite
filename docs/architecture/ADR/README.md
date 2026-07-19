# Architecture decision records

Exported from architect-c4 workspace `ws-vmcp-lite`.

Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs>

| ADR | Status | Title |
|-----|--------|-------|
| [0000](0000-must-solid-dry-tdd-hex.md) | Accepted | MUST: SOLID + DRY + TDD + HEX |
| [0001](0001-stdio-only.md) | Accepted | Stdio-only ingress |
| [0002](0002-runtime-stack.md) | Accepted | Runtime stack: Python FastMCP + Rust/PyO3 |
| [0003](0003-strip-full-vmcp.md) | Accepted | Strip surface inherited from full vmcp |
| [0004](0004-package-layout-open.md) | Accepted | Package layout: single vmcp + internal HEX |
| [0005](0005-docs-structure.md) | Accepted | Documentation structure |
| [0006](0006-sole-tool-query-graphql.md) | Accepted | Sole MCP tool: query_graphql + discovery ladder |
| [0007](0007-query-parallel-mutation-serial.md) | Accepted | Aggregation mode from GraphQL operation kind |
| [0008](0008-registry-sidecar-format.md) | Accepted | Registry + sidecar wire format |
| [0009](0009-error-and-validation.md) | Accepted | Validation + error model |
| [0010](0010-pyo3-callback-boundary.md) | Accepted | PyO3 SchemaEngine to ToolCaller callback boundary |
| [0011](0011-async-bridge-pyo3.md) | Accepted | Async bridge: tokio GraphQL to asyncio ToolCaller |
| [0012](0012-domain-ownership.md) | Accepted | Domain ownership: Python use cases + Rust schema kernel |
| [0013](0013-parallelism-and-call-lock.md) | Accepted | Parallelism scope + per-upstream call_lock |
| [0014](0014-boot-via-composition-root-only.md) | Accepted | Boot only through CompositionRoot |
| [0015](0015-deferred-capabilities.md) | Accepted | Explicitly deferred: hot-reload, HTTP upstream, skills YAML |
| [0016](0016-multi-instance-stdio.md) | Accepted | Multi-instance stdio: one process per host client |
| [0017](0017-lifecycle-shutdown.md) | Accepted | Lifecycle: initialize ready + graceful child reap |
| [0018](0018-graphql-result-contract.md) | Accepted | query_graphql result + error contract |
| [0019](0019-readonly-default.md) | Accepted | Default read_only when hint and sidecar absent |
| [0020](0020-toolchain-naming-coverage.md) | Accepted | Toolchain: PyPI name, Python 3.14, coverage gates |

## Decision themes

- Constitutional constraint: keep SOLID, DRY, TDD, and hexagonal boundaries.
- Product boundary: stdio-only MCP gateway; no HTTP/OAuth/admin/task surface in v1.
- Runtime: Python FastMCP driving adapter plus Rust/PyO3 GraphQL schema kernel.
- Public surface: one MCP tool, `query_graphql`.
- Execution: Query fan-out for read-only tools, sequential Mutation for writes,
  and per-upstream serialization through `call_lock`.
- Operations: partial upstream failure is degraded boot; shutdown reaps children.
