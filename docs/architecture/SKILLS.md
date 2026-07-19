# Architect skills (playbooks)

Source: [hewimetall/architect-c4-mcp](https://github.com/hewimetall/architect-c4-mcp) deploy skills + FastMCP prompts, bound to **ws-vmcp-lite**.

| Skill | File | Role |
|-------|------|------|
| c4_architect_modeling | [`skills/c4_architect_modeling.yaml`](../../skills/c4_architect_modeling.yaml) | C4 + view links |
| architecture_decision_records | [`skills/architecture_decision_records.yaml`](../../skills/architecture_decision_records.yaml) | ADR upsert |
| write_flow | [`skills/write_flow.yaml`](../../skills/write_flow.yaml) | Flows |
| validate_architecture | [`skills/validate_architecture.yaml`](../../skills/validate_architecture.yaml) | validate_model |
| model_c4 | [`skills/model_c4.yaml`](../../skills/model_c4.yaml) | One C4 layer |
| wasm_c4_viewer | [`skills/wasm_c4_viewer.yaml`](../../skills/wasm_c4_viewer.yaml) | All / WASM |
| mermaid_viewer_theme | [`skills/mermaid_viewer_theme.yaml`](../../skills/mermaid_viewer_theme.yaml) | Theme notes |

Cursor agent entrypoint: [`.cursor/skills/architect-c4/SKILL.md`](../../.cursor/skills/architect-c4/SKILL.md)

Viewer: https://architecture.runmcp.ru/view/ws-vmcp-lite  
Flows: https://architecture.runmcp.ru/view/ws-vmcp-lite/flows  
ADRs: https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs
