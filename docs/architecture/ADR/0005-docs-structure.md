# ADR-0005: Documentation structure

- Status: Accepted
- Date: 2026-07-19
- Scope: `vmcp_lite`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0005-docs-structure>

## Context

Full vmcp docs are operator-heavy and include deployment, auth, clients,
benchmarks, and workshops. vmcp-lite needs lean product documentation similar
to mcp-presentation: architecture overview, ADRs, and a thin README.

## Decision

Documentation lives under `docs/`:

- `docs/index.md`
- `docs/architecture/OVERVIEW.md`
- `docs/architecture/DECISIONS.md` or ADR index
- `docs/adr/*.md` or equivalent exported ADRs

README stays focused on quickstart and stack table. Deployment, OAuth, and
benchmark chapters are not part of vmcp-lite v1 documentation.

## Consequences

- architect-c4 workspace `ws-vmcp-lite` is the source of truth during design.
- Markdown export lands in the repo after acceptance.
