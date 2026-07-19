# AGENTS — vmcp-lite

## Architecture-first

Do **not** scaffold application code until package layout (ADR-0004) is decided.

| | |
|---|---|
| architect-c4 workspace | `ws-vmcp-lite` |
| session | `749d972d-5dd9-4bbf-915d-91d3926a648d` |
| MUST | SOLID + DRY + TDD + HEX — [ADR-0000](https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0000-must-solid-dry-tdd-hex) (`accepted`) |
| Skills | [`skills/`](skills/) · [`.cursor/skills/architect-c4/`](.cursor/skills/architect-c4/SKILL.md) |

## Tooling

- MCP server: `architect-c4` → tools `architect-c4__*`
- Playbooks: see [`docs/architecture/SKILLS.md`](docs/architecture/SKILLS.md)

## Product constraints (proposed ADRs)

- Stdio-only ingress (no HTTP/OAuth/admin)
- Python FastMCP + Rust/PyO3 GraphQL
- Strip tasks/proxy/skills-runtime/HTTP-upstreams from full vmcp
