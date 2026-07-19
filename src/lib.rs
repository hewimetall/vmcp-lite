use std::collections::BTreeMap;
use std::sync::Arc;

use pyo3::exceptions::PyValueError;
#[cfg(test)]
use pyo3::ffi::c_str;
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
#[derive(Default)]
struct SchemaEngine {
    tools: Vec<ToolMetadata>,
    tool_caller: Option<Arc<Py<PyAny>>>,
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

    fn set_tool_caller(&mut self, call_tool: Py<PyAny>) {
        self.tool_caller = Some(Arc::new(call_tool));
    }

    fn clear_tool_caller(&mut self) {
        self.tool_caller = None;
    }

    #[pyo3(signature = (query, variables_json=None))]
    fn execute(
        &self,
        py: Python<'_>,
        query: &str,
        variables_json: Option<&str>,
    ) -> PyResult<String> {
        let query = query.to_string();
        let variables_json = variables_json.map(str::to_string);
        Ok(py.detach(|| self.execute_json(&query, variables_json.as_deref())))
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
            tool_caller: None,
        })
    }

    fn execute_json(&self, query: &str, variables_json: Option<&str>) -> String {
        let query = query.trim();
        if query.is_empty() {
            return graphql_error("GraphQL query must not be blank");
        }

        let arguments = match parse_variables(variables_json) {
            Ok(value) => value,
            Err(message) => return graphql_error(&message),
        };

        let fields = top_level_fields(query);
        if fields.is_empty() {
            return graphql_error("GraphQL query must select at least one top-level field");
        }

        let mut data = Map::new();
        let mut tool_jobs = Vec::new();
        for field in fields {
            match field.field_name.as_str() {
                "__typename" => {
                    data.insert(field.response_key, json!("Query"));
                }
                "servers" => {
                    data.insert(field.response_key, Value::Array(self.server_summaries()));
                }
                "search" => {
                    data.insert(field.response_key, Value::Array(self.search_hits()));
                }
                _ => {
                    if let Some(tool) = self
                        .tools
                        .iter()
                        .find(|tool| tool.field_name == field.field_name)
                    {
                        tool_jobs.push(ToolFieldJob {
                            response_key: field.response_key,
                            tool: tool.clone(),
                        });
                    } else {
                        return graphql_error(&format!(
                            "unknown GraphQL field: {}",
                            field.field_name
                        ));
                    }
                }
            }
        }

        for (response_key, value) in self.resolve_tool_jobs(tool_jobs, &arguments) {
            data.insert(response_key, value);
        }

        graphql_success(Value::Object(data))
    }

    fn resolve_tool_jobs(&self, jobs: Vec<ToolFieldJob>, arguments: &Value) -> Vec<(String, Value)> {
        if jobs.len() <= 1 {
            return jobs
                .into_iter()
                .map(|job| {
                    let value = self.resolve_tool_field(&job.tool, arguments);
                    (job.response_key, value)
                })
                .collect();
        }

        std::thread::scope(|scope| {
            let handles: Vec<_> = jobs
                .into_iter()
                .map(|job| {
                    scope.spawn(move || {
                        let value = self.resolve_tool_field(&job.tool, arguments);
                        (job.response_key, value)
                    })
                })
                .collect();

            handles
                .into_iter()
                .map(|handle| {
                    handle
                        .join()
                        .unwrap_or_else(|_| ("".to_string(), tool_error_result("tool resolver panicked")))
                })
                .filter(|(response_key, _)| !response_key.is_empty())
                .collect()
        })
    }

    fn resolve_tool_field(&self, tool: &ToolMetadata, arguments: &Value) -> Value {
        let Some(call_tool) = &self.tool_caller else {
            return tool.placeholder_result();
        };

        let arguments_json = serde_json::to_string(arguments)
            .expect("validated GraphQL variables are serializable as tool arguments");
        let result_json = Python::attach(|py| -> PyResult<String> {
            let result = call_tool.bind(py).call1((
                tool.server.as_str(),
                tool.name.as_str(),
                arguments_json.as_str(),
                if tool.read_only { "query" } else { "mutation" },
            ))?;
            if let Ok(text) = result.extract::<String>() {
                return Ok(text);
            }

            let json_module = py.import("json")?;
            json_module
                .call_method1("dumps", (result,))
                .and_then(|text| text.extract::<String>())
        });

        match result_json {
            Ok(result_json) => serde_json::from_str(&result_json).unwrap_or_else(|err| {
                tool_error_result(&format!("tool caller returned invalid JSON: {err}"))
            }),
            Err(err) => tool_error_result(&format!("tool caller failed: {err}")),
        }
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

#[derive(Clone, Debug)]
struct SelectedField {
    response_key: String,
    field_name: String,
}

#[derive(Clone, Debug)]
struct ToolFieldJob {
    response_key: String,
    tool: ToolMetadata,
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

fn tool_error_result(message: &str) -> Value {
    json!({
        "isError": true,
        "text": message,
        "json": null,
    })
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

fn top_level_fields(query: &str) -> Vec<SelectedField> {
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
                        fields.push(SelectedField {
                            response_key: first_name.to_string(),
                            field_name: query[aliased_start..index].to_string(),
                        });
                    }
                } else {
                    fields.push(SelectedField {
                        response_key: first_name.to_string(),
                        field_name: first_name.to_string(),
                    });
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
    fn known_tool_field_uses_registered_python_tool_caller() {
        let mut engine =
            SchemaEngine::from_catalogue_json(Some(r#"[{"server": "demo", "name": "echo"}]"#))
                .unwrap();
        Python::attach(|py| {
            let module = PyModule::from_code(
                py,
                c_str!(
                    r#"
import json

def call_tool(server, tool, arguments_json, operation):
    return json.dumps({
        "isError": False,
        "text": f"{operation}:{server}.{tool}",
        "json": json.loads(arguments_json),
    })
"#
                ),
                c_str!("callback.py"),
                c_str!("callback"),
            )
            .unwrap();
            engine.set_tool_caller(module.getattr("call_tool").unwrap().unbind());
        });

        let response = decode(
            &engine.execute_json("{ demo_echo { isError text json } }", Some(r#"{"text":"hi"}"#)),
        );

        assert_eq!(response["errors"], json!([]));
        assert_eq!(response["data"]["demo_echo"]["isError"], false);
        assert_eq!(response["data"]["demo_echo"]["text"], "query:demo.echo");
        assert_eq!(response["data"]["demo_echo"]["json"]["text"], "hi");
    }

    #[test]
    fn aliased_tool_fields_are_resolved_concurrently() {
        let mut engine =
            SchemaEngine::from_catalogue_json(Some(r#"[{"server": "demo", "name": "echo"}]"#))
                .unwrap();
        let module: Py<PyModule> = Python::attach(|py| {
            PyModule::from_code(
                py,
                c_str!(
                    r#"
import json
import time

active = 0
max_active = 0

def call_tool(server, tool, arguments_json, operation):
    global active, max_active
    active += 1
    max_active = max(max_active, active)
    try:
        time.sleep(0.05)
        return json.dumps({
            "isError": False,
            "text": f"{server}.{tool}",
            "json": json.loads(arguments_json),
        })
    finally:
        active -= 1
"#
                ),
                c_str!("concurrent_callback.py"),
                c_str!("concurrent_callback"),
            )
            .unwrap()
            .unbind()
        });
        Python::attach(|py| {
            engine.set_tool_caller(module.bind(py).getattr("call_tool").unwrap().unbind());
        });

        let response = decode(&engine.execute_json(
            "{ first: demo_echo { text } second: demo_echo { text } }",
            Some(r#"{"value":1}"#),
        ));
        let max_active: usize = Python::attach(|py| {
            module
                .bind(py)
                .getattr("max_active")
                .unwrap()
                .extract()
                .unwrap()
        });

        assert_eq!(response["errors"], json!([]));
        assert_eq!(response["data"]["first"]["isError"], false);
        assert_eq!(response["data"]["second"]["isError"], false);
        assert!(max_active > 1);
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
