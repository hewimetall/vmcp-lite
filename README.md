# vmcp-lite

Stdio-only virtual MCP gateway (truncated rewrite of [vmcp](https://github.com/hewimetall/vmcp)).

**Status:** architecture-first — C4/ADR/Flows in architect-c4. Implementation pending package-layout decision.

## Architecture

| | |
|---|---|
| Viewer | https://architecture.runmcp.ru/view/ws-vmcp-lite |
| ADRs | https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs |
| Flows | https://architecture.runmcp.ru/view/ws-vmcp-lite/flows |
| MUST | SOLID · DRY · TDD · HEX ([ADR-0000](https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0000-must-solid-dry-tdd-hex)) |
| Skills | [`skills/`](skills/) · [`docs/architecture/SKILLS.md`](docs/architecture/SKILLS.md) |

Stack target: **Python 3.14 + FastMCP** (driving) · **Rust/PyO3** (GraphQL SchemaEngine) · stdio only.

## Agents

See [`AGENT.md`](AGENT.md) and [`.cursor/skills/architect-c4/`](.cursor/skills/architect-c4/SKILL.md).
