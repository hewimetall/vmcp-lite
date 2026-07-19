# vmcp-lite demo

This fixture starts vmcp-lite with two local stdio MCP upstreams:

- `catalog` exposes the read-only `lookup_color` tool.
- `notes` exposes the mutating `remember_note` tool.

Run the gateway from the repository root:

```bash
uv run vmcp-lite --config "$(pwd)/examples/demo/vmcp.toml"
```

Or use the Makefile helper:

```bash
make demo
```

The process serves a single public MCP tool, `query_graphql`, over stdio. The
demo registry and sidecar specs are loaded at startup, and the toy upstream
servers are launched as local Python child processes.
