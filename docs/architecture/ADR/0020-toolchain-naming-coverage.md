# ADR-0020: Toolchain: PyPI name, Python 3.14, coverage gates

- Status: Accepted
- Date: 2026-07-19
- Scope: `vmcp_lite`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0020-toolchain-naming-coverage>

## Context

vmcp-lite must avoid naming collisions with full vmcp while keeping the same
high test bar used by related projects.

## Decision

- Distribution name: `vmcp-lite`.
- Import package: `vmcp`, or `vmcp_lite` if a conflict requires it.
- CLI entrypoint: `vmcp-lite`.
- Do not publish as bare `vmcp` while full Rust vmcp exists.
- Require Python >=3.14 for v1 and run CI on 3.14.
- Document rustup stable and maturin.
- Keep coverage gates: pytest-cov fail-under 98 for Python and
  cargo-llvm-cov fail-under 93 for the PyO3 crate.
- FastMCP is pinned at or above 3.4.4.

## Consequences

- Naming is clear relative to full vmcp.
- The 3.14 toolchain cost is explicit.
- Coverage bars remain high; generated or binding stubs can be excluded rather
  than lowering gates.

## Related flows

- `tdd-port-contract`
