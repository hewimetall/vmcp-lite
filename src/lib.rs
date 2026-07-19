use std::collections::BTreeMap;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyType;
use serde::Serialize;
use serde_json::{json, Map, Value};

/// Minimal GraphQL schema engine exposed to Python.
///
/// This is an ADR-0010/0012 scaffold: Rust owns the schema boundary and Python
/// will later provide the async CallBridge. For now, registered tool fields
/// return a placeholder payload instead of calling upstream MCP servers.
#[pyclass(name = "SchemaEngine")]
#[derive(Debug, Default)]
struct SchemaEngine {
    tools: Vec<ToolMetadata>,
}

#[pymethods]
impl SchemaEngine {
    #[new]
    #[pyo3(signature = (catalogue_json=None))]
    fn new(catalogue_json: Option<&str>) -> PyResult<Self> {
        Self::from_catalogue_json(catalogue_json)
    }

    #[classmethod]
    fn build(_cls: &Bound<'_, PyType>, catalogue_json: &str) -> PyResult<Self> {
        Self::from_catalogue_json(Some(catalogue_json))
    }

    #[classmethod]
    fn from_tool_metadata(_cls: &Bound<'_, PyType>, catalogue_json: &str) -> PyResult<Self> {
        Self::from_catalogue_json(Some(catalogue_json))
    }

    fn describe(&self) -> String {
        format!(
            "vmcp-lite GraphQL schema engine stub ({} tool fields)",
            self.tools.len()
        )
    }

    fn tool_count(&self) -> usize {
        self.tools.len()
    }

    #[pyo3(signature = (query, variables_json=None))]
    fn execute(&self, query: &str, variables_json: Option<&str>) -> PyResult<String> {
        Ok(self.execute_json(query, variables_json))
    }
}

impl SchemaEngine {
    fn from_catalogue_json(catalogue_json: Option<&str>) -> PyResult<Self> {
        let Some(catalogue_json) = catalogue_json else {
            return Ok(Self::default());
        };
        if catalogue_json.trim().is_empty() {
            return Ok(Self::default());
        }

        let value: Value = serde_json::from_str(catalogue_json)
            .map_err(|err| PyValueError::new_err(format!("invalid catalogue JSON: {err}")))?;
        Ok(Self {
            tools: parse_tool_catalogue(&value),
        })
    }

    fn execute_json(&self, query: &str, variables_json: Option<&str>) -> String {
        let query = query.trim();
        if query.is_empty() {
            return graphql_error("GraphQL query must not be blank");
        }

        if let Err(message) = parse_variables(variables_json) {
            return graphql_error(&message);
        }

        let fields = top_level_fields(query);
        if fields.is_empty() {
            return graphql_error("GraphQL query must select at least one top-level field");
        }

        let mut data = Map::new();
        for field in fields {
            match field.as_str() {
                "__typename" => {
                    data.insert(field, json!("Query"));
                }
                "servers" => {
                    data.insert(field, Value::Array(self.server_summaries()));
                }
                "search" => {
                    data.insert(field, Value::Array(self.search_hits()));
                }
                _ => {
                    if let Some(tool) = self.tools.iter().find(|tool| tool.field_name == field) {
                        // TODO(ADR-0010): hand this field off to a tokio worker that awaits the
                        // Python CallBridge instead of returning a fixed placeholder payload.
                        data.insert(field, tool.placeholder_result());
                    } else {
                        return graphql_error(&format!("unknown GraphQL field: {field}"));
                    }
                }
            }
        }

        graphql_success(Value::Object(data))
    }

    fn server_summaries(&self) -> Vec<Value> {
        let mut servers: BTreeMap<&str, ServerSummary<'_>> = BTreeMap::new();
        for tool in &self.tools {
            let summary = servers
                .entry(tool.server.as_str())
                .or_insert_with(|| ServerSummary {
                    name: tool.server.as_str(),
                    description: tool.server_description.as_deref(),
                    tool_count: 0,
                    read_only_count: 0,
                });
            summary.tool_count += 1;
            if tool.read_only {
                summary.read_only_count += 1;
            }
            if summary.description.is_none() {
                summary.description = tool.server_description.as_deref();
            }
        }

        servers
            .into_values()
            .map(|server| {
                json!({
                    "name": server.name,
                    "description": server.description.unwrap_or(""),
                    "toolCount": server.tool_count,
                    "readOnlyCount": server.read_only_count,
                })
            })
            .collect()
    }

    fn search_hits(&self) -> Vec<Value> {
        self.tools
            .iter()
            .map(|tool| {
                json!({
                    "server": tool.server,
                    "tool": tool.name,
                    "field": tool.field_name,
                    "description": tool.description,
                    "readOnly": tool.read_only,
                })
            })
            .collect()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ToolMetadata {
    server: String,
    server_description: Option<String>,
    name: String,
    description: String,
    read_only: bool,
    field_name: String,
}

impl ToolMetadata {
    fn placeholder_result(&self) -> Value {
        json!({
            "isError": true,
            "text": format!(
                "SchemaEngine stub resolved {}.{} but CallBridge is not wired yet",
                self.server, self.name
            ),
            "json": null,
        })
    }
}

#[derive(Clone, Debug)]
struct ServerSummary<'a> {
    name: &'a str,
    description: Option<&'a str>,
    tool_count: usize,
    read_only_count: usize,
}

#[derive(Serialize)]
struct GraphqlResponse {
    data: Value,
    errors: Vec<GraphqlError>,
}

#[derive(Serialize)]
struct GraphqlError {
    message: String,
}

fn graphql_success(data: Value) -> String {
    serde_json::to_string(&GraphqlResponse {
        data,
        errors: Vec::new(),
    })
    .expect("GraphQL success response is serializable")
}

fn graphql_error(message: &str) -> String {
    serde_json::to_string(&GraphqlResponse {
        data: Value::Null,
        errors: vec![GraphqlError {
            message: message.to_string(),
        }],
    })
    .expect("GraphQL error response is serializable")
}

fn parse_variables(variables_json: Option<&str>) -> Result<Value, String> {
    let Some(variables_json) = variables_json else {
        return Ok(Value::Object(Map::new()));
    };
    if variables_json.trim().is_empty() {
        return Ok(Value::Object(Map::new()));
    }

    let value: Value = serde_json::from_str(variables_json)
        .map_err(|err| format!("invalid variables JSON: {err}"))?;
    if value.is_object() {
        Ok(value)
    } else {
        Err("variables_json must decode to a JSON object".to_string())
    }
}

fn parse_tool_catalogue(value: &Value) -> Vec<ToolMetadata> {
    let mut tools = Vec::new();
    collect_tool_metadata(value, None, None, &mut tools);
    tools
}

fn collect_tool_metadata(
    value: &Value,
    inherited_server: Option<&str>,
    inherited_server_description: Option<&str>,
    tools: &mut Vec<ToolMetadata>,
) {
    match value {
        Value::Array(items) => {
            for item in items {
                collect_tool_metadata(item, inherited_server, inherited_server_description, tools);
            }
        }
        Value::Object(object) => {
            let server = string_field(object, &["server", "server_id", "serverId", "server_name"])
                .or(inherited_server)
                .map(str::to_string);
            let server_description =
                string_field(object, &["serverDescription", "server_description"])
                    .or(inherited_server_description)
                    .map(str::to_string);

            if let Some(children) = object.get("tools").and_then(Value::as_array) {
                for child in children {
                    collect_tool_metadata(
                        child,
                        server.as_deref(),
                        server_description.as_deref(),
                        tools,
                    );
                }
                return;
            }

            let Some(server) = server else {
                return;
            };
            let Some(name) = string_field(object, &["name", "tool", "tool_name", "toolName"])
            else {
                return;
            };
            let description = string_field(object, &["description"])
                .unwrap_or_default()
                .to_string();
            let read_only = bool_field(object, &["readOnly", "read_only", "readOnlyHint"])
                .or_else(|| {
                    object
                        .get("annotations")
                        .and_then(Value::as_object)
                        .and_then(|annotations| bool_field(annotations, &["readOnlyHint"]))
                })
                .unwrap_or(true);
            let field_name = graphql_field_name(&server, name);

            tools.push(ToolMetadata {
                server,
                server_description,
                name: name.to_string(),
                description,
                read_only,
                field_name,
            });
        }
        _ => {}
    }
}

fn string_field<'a>(object: &'a Map<String, Value>, keys: &[&str]) -> Option<&'a str> {
    for key in keys {
        match object.get(*key) {
            Some(Value::String(value)) if !value.trim().is_empty() => return Some(value.trim()),
            Some(Value::Object(nested)) => {
                if let Some(Value::String(value)) = nested.get("value") {
                    if !value.trim().is_empty() {
                        return Some(value.trim());
                    }
                }
            }
            _ => {}
        }
    }
    None
}

fn bool_field(object: &Map<String, Value>, keys: &[&str]) -> Option<bool> {
    for key in keys {
        if let Some(Value::Bool(value)) = object.get(*key) {
            return Some(*value);
        }
    }
    None
}

fn graphql_field_name(server: &str, tool: &str) -> String {
    format!(
        "{}_{}",
        sanitize_graphql_name(server),
        sanitize_graphql_name(tool)
    )
}

fn sanitize_graphql_name(value: &str) -> String {
    let mut output = String::new();
    for character in value.chars() {
        if character.is_ascii_alphanumeric() || character == '_' {
            output.push(character);
        } else {
            output.push('_');
        }
    }

    if output.is_empty() {
        output.push('_');
    }

    if output
        .as_bytes()
        .first()
        .is_some_and(|first| first.is_ascii_digit())
    {
        output.insert(0, '_');
    }

    output
}

fn top_level_fields(query: &str) -> Vec<String> {
    let Some(open_brace) = query.find('{') else {
        return Vec::new();
    };

    let mut fields = Vec::new();
    let mut depth = 0usize;
    let mut paren_depth = 0usize;
    let mut index = open_brace;
    let bytes = query.as_bytes();

    while index < bytes.len() {
        match bytes[index] as char {
            '"' => {
                index += 1;
                while index < bytes.len() {
                    match bytes[index] as char {
                        '\\' => index += 2,
                        '"' => {
                            index += 1;
                            break;
                        }
                        _ => index += 1,
                    }
                }
            }
            '{' => {
                depth += 1;
                index += 1;
            }
            '}' => {
                depth = depth.saturating_sub(1);
                index += 1;
            }
            '(' if depth == 1 => {
                paren_depth += 1;
                index += 1;
            }
            ')' if depth == 1 => {
                paren_depth = paren_depth.saturating_sub(1);
                index += 1;
            }
            character if depth == 1 && paren_depth == 0 && is_graphql_name_start(character) => {
                let start = index;
                index += 1;
                while index < bytes.len() && is_graphql_name_continue(bytes[index] as char) {
                    index += 1;
                }
                let first_name = &query[start..index];
                index = skip_whitespace(query, index);
                if bytes.get(index).is_some_and(|byte| *byte == b':') {
                    index += 1;
                    index = skip_whitespace(query, index);
                    if index < bytes.len() && is_graphql_name_start(bytes[index] as char) {
                        let aliased_start = index;
                        index += 1;
                        while index < bytes.len() && is_graphql_name_continue(bytes[index] as char)
                        {
                            index += 1;
                        }
                        fields.push(query[aliased_start..index].to_string());
                    }
                } else {
                    fields.push(first_name.to_string());
                }
            }
            _ => {
                index += 1;
            }
        }
    }

    fields
}

fn skip_whitespace(query: &str, mut index: usize) -> usize {
    let bytes = query.as_bytes();
    while index < bytes.len() && bytes[index].is_ascii_whitespace() {
        index += 1;
    }
    index
}

fn is_graphql_name_start(character: char) -> bool {
    character.is_ascii_alphabetic() || character == '_'
}

fn is_graphql_name_continue(character: char) -> bool {
    character.is_ascii_alphanumeric() || character == '_'
}

#[pyfunction]
fn graphql_stub() -> &'static str {
    "vmcp-lite _graphql extension loaded"
}

#[pymodule]
fn _graphql(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SchemaEngine>()?;
    m.add_function(wrap_pyfunction!(graphql_stub, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn decode(response: &str) -> Value {
        serde_json::from_str(response).unwrap()
    }

    #[test]
    fn empty_engine_answers_typename() {
        let engine = SchemaEngine::default();

        let response = decode(&engine.execute_json("{ __typename }", None));

        assert_eq!(response["data"]["__typename"], "Query");
        assert_eq!(response["errors"], json!([]));
    }

    #[test]
    fn build_parses_simplified_tool_metadata_list() {
        let engine = SchemaEngine::from_catalogue_json(Some(
            r#"[
                {
                    "server": "demo",
                    "serverDescription": "Demo upstream",
                    "name": "echo",
                    "description": "Echo input",
                    "readOnly": true
                },
                {
                    "server_id": {"value": "writer"},
                    "tool_name": "save-file",
                    "read_only": false
                }
            ]"#,
        ))
        .unwrap();

        assert_eq!(engine.tool_count(), 2);
        assert_eq!(engine.tools[0].field_name, "demo_echo");
        assert_eq!(engine.tools[1].field_name, "writer_save_file");
    }

    #[test]
    fn build_parses_grouped_catalogue() {
        let engine = SchemaEngine::from_catalogue_json(Some(
            r#"{
                "tools": [
                    {
                        "server": "ignored"
                    }
                ],
                "server": "also-ignored"
            }"#,
        ))
        .unwrap();
        assert_eq!(engine.tool_count(), 0);

        let engine = SchemaEngine::from_catalogue_json(Some(
            r#"[
                {
                    "server": "demo",
                    "server_description": "Demo upstream",
                    "tools": [
                        {"name": "echo"},
                        {"name": "write", "annotations": {"readOnlyHint": false}}
                    ]
                }
            ]"#,
        ))
        .unwrap();

        assert_eq!(engine.tool_count(), 2);
        assert!(!engine.tools[1].read_only);
    }

    #[test]
    fn servers_and_search_use_graphql_json_contract() {
        let engine = SchemaEngine::from_catalogue_json(Some(
            r#"[{"server": "demo", "name": "echo", "description": "Echo input"}]"#,
        ))
        .unwrap();

        let response = decode(
            &engine.execute_json("{ servers { name } search(q: \"echo\") { field } }", None),
        );

        assert_eq!(response["errors"], json!([]));
        assert_eq!(response["data"]["servers"][0]["name"], "demo");
        assert_eq!(response["data"]["search"][0]["field"], "demo_echo");
    }

    #[test]
    fn known_tool_field_returns_call_bridge_placeholder() {
        let engine =
            SchemaEngine::from_catalogue_json(Some(r#"[{"server": "demo", "name": "echo"}]"#))
                .unwrap();

        let response =
            decode(&engine.execute_json("{ demo_echo { isError text json } }", Some("{}")));

        assert_eq!(response["errors"], json!([]));
        assert_eq!(response["data"]["demo_echo"]["isError"], true);
        assert!(response["data"]["demo_echo"]["text"]
            .as_str()
            .unwrap()
            .contains("CallBridge is not wired yet"));
    }

    #[test]
    fn unknown_field_returns_error_contract() {
        let engine = SchemaEngine::default();

        let response = decode(&engine.execute_json("{ unknown }", None));

        assert_eq!(response["data"], Value::Null);
        assert_eq!(
            response["errors"][0]["message"],
            "unknown GraphQL field: unknown"
        );
    }

    #[test]
    fn invalid_variables_return_error_contract() {
        let engine = SchemaEngine::default();

        let response = decode(&engine.execute_json("{ __typename }", Some("[]")));

        assert_eq!(response["data"], Value::Null);
        assert_eq!(
            response["errors"][0]["message"],
            "variables_json must decode to a JSON object"
        );
    }

    #[test]
    fn module_registers_schema_engine() {
        Python::attach(|py| {
            let module = PyModule::new(py, "_graphql").unwrap();
            _graphql(&module).unwrap();
            assert!(module.getattr("SchemaEngine").is_ok());
            assert!(module.getattr("graphql_stub").is_ok());
        });
    }
}
