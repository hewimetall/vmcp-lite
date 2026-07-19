# ADR-0008: Registry + sidecar wire format

- Status: Accepted
- Date: 2026-07-19
- Scope: `registry_cfg`
- Viewer: <https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0008-registry-sidecar-format>

## Context

vmcp-lite needs one schema source for upstream catalog data. Full vmcp registry
data is useful, but its HTTP transport entries are outside lite scope.

## Decision

Keep a wire-compatible subset of full vmcp `registry.json`:

- `upstreams[]` with name, optional description, command, args, env, optional
  cwd, optional `sidecar_spec`, and enabled flag.
- Transport is fixed to stdio; HTTP entries are omitted or rejected.
- Sidecar specs live at `specs/<server>.json` and include
  `tools[].name`, `read_only`, and optional description.
- Schema is rebuilt at boot only; no `tools.lock.json` drift hot-swap in v1.
- `vmcp.toml` contains only minimal registry, spec, GraphQL limit, and upstream
  timeout settings.

## Consequences

- Demo stdio registries from full vmcp are mostly reusable.
- HTTP upstream entries fail validation.
- Boot is simpler and deterministic.

## Related flows

- `boot-aggregation`
- `config-load-env-override`
- `sidecar-readonly-override`
