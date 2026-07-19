---
name: architect-c4
description: >
  Model C4 + ADR + Flows for vmcp-lite via architect-c4 MCP (ports & adapters).
  Use when designing architecture, writing ADRs/flows, validating the model,
  or opening viewer links. MUST SOLID+DRY+TDD+HEX (ADR-0000 accepted).
---

# architect-c4 (vmcp-lite)

## Bound session

| | |
|---|---|
| session_id | `749d972d-5dd9-4bbf-915d-91d3926a648d` |
| project_id | `vmcp-lite` |
| workspace_id | `ws-vmcp-lite` |
| viewer | https://architecture.runmcp.ru/view/ws-vmcp-lite |
| constitutional ADR | [0000 MUST SOLID DRY TDD HEX](https://architecture.runmcp.ru/view/ws-vmcp-lite/adrs/0000-must-solid-dry-tdd-hex) (`accepted`) |

## Tool server

Call MCP tools on server `architect-c4` with names `architect-c4__*`.

Atom canon (this deployment): **relationships only between `code` / `person` / `software_system` / `external`**. Do not create container→container or component→component edges.

## Playbooks (YAML)

Operator skills live in [`skills/`](../../../skills/):

| Skill | When |
|-------|------|
| `c4_architect_modeling` | C4 layers, drill-down, view links |
| `architecture_decision_records` | Lock a decision (ADR) |
| `write_flow` | c4_dynamic / sequence / state |
| `validate_architecture` | validate_model + fix problems |
| `model_c4` | One C4 layer at a time |
| `wasm_c4_viewer` | All-layers / WASM scene |
| `mermaid_viewer_theme` | Readable Mermaid theme notes |

## Startup batch (every architect turn)

1. `architect-c4__get_view_links` (`workspace_id=ws-vmcp-lite`)
2. `architect-c4__list_adrs` + `architect-c4__list_flows`
3. `architect-c4__validate_model`
4. Prefer `get_overview_diagram` / `get_layer_diagram` / `get_flow_diagram` for URLs — never invent bases

## Write rules

- ADR status from agent: only `draft` | `proposed`. Process transitions via `set_adr_status`.
- ADR `scope_element_id` must be a real element id.
- Flow `steps[].from_id` / `to_id` must exist.
- `sequence` / `state` flows use Mermaid `body`.
- HEX: domain+ports inward; FastMCP driving; GraphQL/pool/config driven.
- Flat FastMCP without domain/ports is **forbidden** (ADR-0000 / ADR-0004).

## Do not

- Scaffold application code until package layout ADR-0004 is decided (A or B).
- Reintroduce HTTP ingress / OAuth / admin / run_task (ADR-0001, ADR-0003).
