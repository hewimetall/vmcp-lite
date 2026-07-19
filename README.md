# vmcp-lite

vmcp-lite is a stdio-only HEX virtual MCP gateway that will expose one
`query_graphql` tool through FastMCP, compose Python domain/use-case boundaries,
and delegate GraphQL schema work to a Rust/PyO3 extension module.

## Install

```bash
pip install vmcp-lite-mcp
# or
uv add vmcp-lite-mcp
```

## Run

```bash
uv run vmcp-lite
# or
uvx vmcp-lite-mcp
```
