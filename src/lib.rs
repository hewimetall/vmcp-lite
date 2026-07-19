use std::collections::BTreeMap;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;

use futures::future::join_all;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyType;
use serde::Serialize;
use serde_json::{json, Map, Value};
use tokio::runtime::{Builder, Runtime};
use tokio::sync::{mpsc, oneshot};

const TOOL_REQUEST_QUEUE_SIZE: usize = 1024;
const DEFAULT_RECEIVE_TIMEOUT_MS: u64 = 100;

/// Minimal GraphQL schema engine exposed to Python.
///
/// Rust owns GraphQL execution and an ADR-0011 channel bridge. Tool resolvers
/// enqueue requests on a tokio mpsc channel, await a per-call oneshot response,
/// and never call Python while holding the GIL.
#[pyclass(name = "SchemaEngine")]
struct SchemaEngine {
    tools: Vec<ToolMetadata>,
    runtime: Arc<Runtime>,
    call_bridge: Arc<TokioCallBridge>,
}

impl Default for SchemaEngine {
    fn default() -> Self {
        Self {
            tools: Vec::new(),
            runtime: Arc::new(build_runtime()),
            call_bridge: Arc::new(TokioCallBridge::new()),
        }
    }
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

    fn attach_call_bridge(&self) {
        self.call_bridge.attach();
    }

    fn detach_call_bridge(&self) {
        self.call_bridge.detach();
    }

    #[pyo3(signature = (timeout_ms = DEFAULT_RECEIVE_TIMEOUT_MS))]
    fn receive_tool_call(&self, py: Python<'_>, timeout_ms: u64) -> PyResult<Option<String>> {
        let call_bridge = Arc::clone(&self.call_bridge);
        let runtime = Arc::clone(&self.runtime);
        py.detach(move || {
            call_bridge
                .receive_json(&runtime, Duration::from_millis(timeout_ms))
                .map_err(PyValueError::new_err)
        })
    }

    fn respond_tool_call(&self, request_id: &str, result_json: &str) -> bool {
        self.call_bridge
            .respond(request_id, result_json.to_string())
    }

    fn fail_tool_call(&self, request_id: &str, message: &str) -> bool {
        let result_json = serde_json::to_string(&tool_error_result(message))
            .expect("tool error response is serializable");
        self.call_bridge.respond(request_id, result_json)
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
            runtime: Arc::new(build_runtime()),
            call_bridge: Arc::new(TokioCallBridge::new()),
        })
    }

    fn execute_json(&self, query: &str, variables_json: Option<&str>) -> String {
        self.runtime
            .block_on(self.execute_json_async(query, variables_json))
    }

    async fn execute_json_async(&self, query: &str, variables_json: Option<&str>) -> String {
        let query = query.trim();
        if query.is_empty() {
            return graphql_error("GraphQL query must not be blank");
        }

        let variables = match parse_variables(variables_json) {
            Ok(value) => value,
            Err(message) => return graphql_error(&message),
        };
        let variables = variables
            .as_object()
            .expect("parse_variables returns a JSON object");

        let fields = match parse_top_level_fields(query, variables) {
            Ok(fields) if !fields.is_empty() => fields,
            Ok(_) => {
                return graphql_error("GraphQL query must select at least one top-level field");
            }
            Err(message) => return graphql_error(&message),
        };

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
                            arguments: field.arguments,
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

        for (response_key, value) in self.resolve_tool_jobs(tool_jobs).await {
            data.insert(response_key, value);
        }

        graphql_success(Value::Object(data))
    }

    async fn resolve_tool_jobs(&self, jobs: Vec<ToolFieldJob>) -> Vec<(String, Value)> {
        if jobs.len() <= 1 {
            let mut results = Vec::with_capacity(jobs.len());
            for job in jobs {
                let value = self
                    .resolve_tool_field(&job.tool, Value::Object(job.arguments))
                    .await;
                results.push((job.response_key, value));
            }
            return results;
        }

        join_all(jobs.into_iter().map(|job| async move {
            let value = self
                .resolve_tool_field(&job.tool, Value::Object(job.arguments))
                .await;
            (job.response_key, value)
        }))
        .await
    }

    async fn resolve_tool_field(&self, tool: &ToolMetadata, arguments: Value) -> Value {
        if !self.call_bridge.is_attached() {
            return tool.placeholder_result();
        }

        match self
            .call_bridge
            .call_tool(
                &tool.server,
                &tool.name,
                arguments,
                if tool.read_only { "query" } else { "mutation" },
            )
            .await
        {
            Ok(value) => value,
            Err(message) => tool_error_result(&message),
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
    arguments: Map<String, Value>,
}

#[derive(Clone, Debug)]
struct ToolFieldJob {
    response_key: String,
    tool: ToolMetadata,
    arguments: Map<String, Value>,
}

#[derive(Debug)]
struct TokioCallBridge {
    sender: mpsc::Sender<BridgeEnvelope>,
    receiver: Mutex<mpsc::Receiver<BridgeEnvelope>>,
    pending: Mutex<HashMap<String, oneshot::Sender<String>>>,
    attached: AtomicBool,
    next_request_id: AtomicU64,
}

#[derive(Debug)]
struct BridgeEnvelope {
    request_id: String,
    server: String,
    tool: String,
    arguments: Value,
    operation: String,
}

#[derive(Serialize)]
struct BridgeRequestPayload<'a> {
    request_id: &'a str,
    server: &'a str,
    tool: &'a str,
    arguments: &'a Value,
    operation: &'a str,
}

impl TokioCallBridge {
    fn new() -> Self {
        let (sender, receiver) = mpsc::channel(TOOL_REQUEST_QUEUE_SIZE);
        Self {
            sender,
            receiver: Mutex::new(receiver),
            pending: Mutex::new(HashMap::new()),
            attached: AtomicBool::new(false),
            next_request_id: AtomicU64::new(1),
        }
    }

    fn attach(&self) {
        self.attached.store(true, Ordering::SeqCst);
    }

    fn detach(&self) {
        self.attached.store(false, Ordering::SeqCst);
        self.cancel_pending("CallBridge is detached");
    }

    fn is_attached(&self) -> bool {
        self.attached.load(Ordering::SeqCst)
    }

    async fn call_tool(
        &self,
        server: &str,
        tool: &str,
        arguments: Value,
        operation: &str,
    ) -> Result<Value, String> {
        let request_id = format!(
            "tool-call-{}",
            self.next_request_id.fetch_add(1, Ordering::SeqCst)
        );
        let (response_tx, response_rx) = oneshot::channel();
        self.pending
            .lock()
            .expect("pending bridge responses mutex poisoned")
            .insert(request_id.clone(), response_tx);

        let envelope = BridgeEnvelope {
            request_id: request_id.clone(),
            server: server.to_string(),
            tool: tool.to_string(),
            arguments,
            operation: operation.to_string(),
        };

        if self.sender.send(envelope).await.is_err() {
            self.pending
                .lock()
                .expect("pending bridge responses mutex poisoned")
                .remove(&request_id);
            return Err("CallBridge request channel is closed".to_string());
        }

        let response_json = response_rx
            .await
            .map_err(|_| "CallBridge response channel is closed".to_string())?;
        serde_json::from_str(&response_json)
            .map_err(|err| format!("CallBridge returned invalid JSON: {err}"))
    }

    fn receive_json(&self, runtime: &Runtime, timeout: Duration) -> Result<Option<String>, String> {
        let envelope = if timeout.is_zero() {
            let mut receiver = self
                .receiver
                .lock()
                .expect("bridge request receiver mutex poisoned");
            match receiver.try_recv() {
                Ok(envelope) => Some(envelope),
                Err(mpsc::error::TryRecvError::Empty) => None,
                Err(mpsc::error::TryRecvError::Disconnected) => None,
            }
        } else {
            let mut receiver = self
                .receiver
                .lock()
                .expect("bridge request receiver mutex poisoned");
            runtime.block_on(async {
                tokio::time::timeout(timeout, receiver.recv())
                    .await
                    .unwrap_or(None)
            })
        };

        envelope
            .map(|envelope| {
                serde_json::to_string(&BridgeRequestPayload {
                    request_id: envelope.request_id.as_str(),
                    server: envelope.server.as_str(),
                    tool: envelope.tool.as_str(),
                    arguments: &envelope.arguments,
                    operation: envelope.operation.as_str(),
                })
                .map_err(|err| format!("CallBridge request was not serializable: {err}"))
            })
            .transpose()
    }

    fn respond(&self, request_id: &str, result_json: String) -> bool {
        self.pending
            .lock()
            .expect("pending bridge responses mutex poisoned")
            .remove(request_id)
            .is_some_and(|response_tx| response_tx.send(result_json).is_ok())
    }

    fn cancel_pending(&self, message: &str) {
        let result_json = serde_json::to_string(&tool_error_result(message))
            .expect("tool error response is serializable");
        let pending = self
            .pending
            .lock()
            .expect("pending bridge responses mutex poisoned")
            .drain()
            .map(|(_, response_tx)| response_tx)
            .collect::<Vec<_>>();

        for response_tx in pending {
            let _ = response_tx.send(result_json.clone());
        }
    }
}

fn build_runtime() -> Runtime {
    Builder::new_multi_thread()
        .thread_name("vmcp-lite-graphql")
        .enable_time()
        .build()
        .expect("vmcp-lite GraphQL tokio runtime must start")
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

fn parse_top_level_fields(
    query: &str,
    variables: &Map<String, Value>,
) -> Result<Vec<SelectedField>, String> {
    GraphqlFieldParser::new(query, variables).parse()
}

struct GraphqlFieldParser<'a> {
    query: &'a str,
    variables: &'a Map<String, Value>,
    index: usize,
}

impl<'a> GraphqlFieldParser<'a> {
    fn new(query: &'a str, variables: &'a Map<String, Value>) -> Self {
        Self {
            query,
            variables,
            index: 0,
        }
    }

    fn parse(mut self) -> Result<Vec<SelectedField>, String> {
        self.skip_ignored();
        if self.peek_byte() == Some(b'{') {
            return self.parse_selection_set();
        }

        let operation = self.parse_name()?;
        if !matches!(operation.as_str(), "query" | "mutation" | "subscription") {
            return Err(format!("unsupported GraphQL operation: {operation}"));
        }

        self.skip_ignored();
        if self.peek_byte().is_some_and(is_graphql_name_start_byte) {
            let _operation_name = self.parse_name()?;
            self.skip_ignored();
        }
        if self.peek_byte() == Some(b'(') {
            self.skip_balanced(b'(', b')')?;
            self.skip_ignored();
        }
        self.skip_directives()?;
        self.parse_selection_set()
    }

    fn parse_selection_set(&mut self) -> Result<Vec<SelectedField>, String> {
        self.expect_byte(b'{')?;
        let mut fields = Vec::new();

        loop {
            self.skip_ignored();
            match self.peek_byte() {
                Some(b'}') => {
                    self.index += 1;
                    return Ok(fields);
                }
                Some(_) => fields.push(self.parse_field()?),
                None => return Err("unterminated GraphQL selection set".to_string()),
            }
        }
    }

    fn parse_field(&mut self) -> Result<SelectedField, String> {
        if self.query[self.index..].starts_with("...") {
            return Err("GraphQL fragments are not supported by vmcp-lite yet".to_string());
        }

        let first_name = self.parse_name()?;
        self.skip_ignored();

        let (response_key, field_name) = if self.peek_byte() == Some(b':') {
            self.index += 1;
            self.skip_ignored();
            (first_name, self.parse_name()?)
        } else {
            (first_name.clone(), first_name)
        };

        self.skip_ignored();
        let arguments = if self.peek_byte() == Some(b'(') {
            self.parse_arguments()?
        } else {
            Map::new()
        };

        self.skip_ignored();
        self.skip_directives()?;
        self.skip_ignored();
        if self.peek_byte() == Some(b'{') {
            self.skip_balanced(b'{', b'}')?;
        }

        Ok(SelectedField {
            response_key,
            field_name,
            arguments,
        })
    }

    fn parse_arguments(&mut self) -> Result<Map<String, Value>, String> {
        self.expect_byte(b'(')?;
        let mut arguments = Map::new();

        loop {
            self.skip_ignored();
            if self.peek_byte() == Some(b')') {
                self.index += 1;
                return Ok(arguments);
            }

            let name = self.parse_name()?;
            self.skip_ignored();
            self.expect_byte(b':')?;
            self.skip_ignored();
            let value = self.parse_value()?;
            arguments.insert(name, value);
            self.skip_ignored();
        }
    }

    fn parse_value(&mut self) -> Result<Value, String> {
        self.skip_ignored();
        match self.peek_byte() {
            Some(b'$') => self.parse_variable_reference(),
            Some(b'"') => self.parse_string(),
            Some(b'-' | b'0'..=b'9') => self.parse_number(),
            Some(b'[') => self.parse_list(),
            Some(b'{') => self.parse_input_object(),
            Some(byte) if is_graphql_name_start_byte(byte) => self.parse_named_value(),
            Some(byte) => Err(format!(
                "unexpected character in GraphQL argument value: {}",
                byte as char
            )),
            None => Err("unexpected end of GraphQL argument value".to_string()),
        }
    }

    fn parse_variable_reference(&mut self) -> Result<Value, String> {
        self.expect_byte(b'$')?;
        let name = self.parse_name()?;
        self.variables
            .get(&name)
            .cloned()
            .ok_or_else(|| format!("missing GraphQL variable: ${name}"))
    }

    fn parse_string(&mut self) -> Result<Value, String> {
        let start = self.index;
        self.index += 1;
        while let Some(byte) = self.peek_byte() {
            match byte {
                b'\\' => {
                    self.index += 1;
                    if self.peek_byte().is_none() {
                        return Err("unterminated GraphQL string literal".to_string());
                    }
                    self.index += 1;
                }
                b'"' => {
                    self.index += 1;
                    return serde_json::from_str(&self.query[start..self.index])
                        .map_err(|err| format!("invalid GraphQL string literal: {err}"));
                }
                _ => self.index += 1,
            }
        }
        Err("unterminated GraphQL string literal".to_string())
    }

    fn parse_number(&mut self) -> Result<Value, String> {
        let start = self.index;
        if self.peek_byte() == Some(b'-') {
            self.index += 1;
        }
        self.consume_digits();
        if self.peek_byte() == Some(b'.') {
            self.index += 1;
            self.consume_digits();
        }
        if matches!(self.peek_byte(), Some(b'e' | b'E')) {
            self.index += 1;
            if matches!(self.peek_byte(), Some(b'+' | b'-')) {
                self.index += 1;
            }
            self.consume_digits();
        }

        let raw = &self.query[start..self.index];
        serde_json::from_str(raw).map_err(|err| format!("invalid GraphQL number literal: {err}"))
    }

    fn parse_list(&mut self) -> Result<Value, String> {
        self.expect_byte(b'[')?;
        let mut values = Vec::new();
        loop {
            self.skip_ignored();
            if self.peek_byte() == Some(b']') {
                self.index += 1;
                return Ok(Value::Array(values));
            }
            values.push(self.parse_value()?);
            self.skip_ignored();
        }
    }

    fn parse_input_object(&mut self) -> Result<Value, String> {
        self.expect_byte(b'{')?;
        let mut object = Map::new();
        loop {
            self.skip_ignored();
            if self.peek_byte() == Some(b'}') {
                self.index += 1;
                return Ok(Value::Object(object));
            }
            let key = self.parse_name()?;
            self.skip_ignored();
            self.expect_byte(b':')?;
            self.skip_ignored();
            let value = self.parse_value()?;
            object.insert(key, value);
            self.skip_ignored();
        }
    }

    fn parse_named_value(&mut self) -> Result<Value, String> {
        match self.parse_name()?.as_str() {
            "true" => Ok(Value::Bool(true)),
            "false" => Ok(Value::Bool(false)),
            "null" => Ok(Value::Null),
            enum_value => Ok(Value::String(enum_value.to_string())),
        }
    }

    fn parse_name(&mut self) -> Result<String, String> {
        self.skip_ignored();
        let start = self.index;
        let Some(byte) = self.peek_byte() else {
            return Err("expected GraphQL name, found end of input".to_string());
        };
        if !is_graphql_name_start_byte(byte) {
            return Err(format!("expected GraphQL name, found {}", byte as char));
        }
        self.index += 1;
        while self.peek_byte().is_some_and(is_graphql_name_continue_byte) {
            self.index += 1;
        }
        Ok(self.query[start..self.index].to_string())
    }

    fn skip_directives(&mut self) -> Result<(), String> {
        loop {
            self.skip_ignored();
            if self.peek_byte() != Some(b'@') {
                return Ok(());
            }
            self.index += 1;
            let _directive_name = self.parse_name()?;
            self.skip_ignored();
            if self.peek_byte() == Some(b'(') {
                self.skip_balanced(b'(', b')')?;
            }
        }
    }

    fn skip_balanced(&mut self, open: u8, close: u8) -> Result<(), String> {
        self.expect_byte(open)?;
        let mut depth = 1usize;
        while let Some(byte) = self.peek_byte() {
            match byte {
                b'"' => self.skip_string_literal()?,
                b'#' => self.skip_comment(),
                value if value == open => {
                    depth += 1;
                    self.index += 1;
                }
                value if value == close => {
                    depth -= 1;
                    self.index += 1;
                    if depth == 0 {
                        return Ok(());
                    }
                }
                _ => self.index += 1,
            }
        }
        Err(format!(
            "unterminated GraphQL balanced block: {}",
            open as char
        ))
    }

    fn skip_string_literal(&mut self) -> Result<(), String> {
        self.expect_byte(b'"')?;
        while let Some(byte) = self.peek_byte() {
            match byte {
                b'\\' => {
                    self.index += 1;
                    if self.peek_byte().is_none() {
                        return Err("unterminated GraphQL string literal".to_string());
                    }
                    self.index += 1;
                }
                b'"' => {
                    self.index += 1;
                    return Ok(());
                }
                _ => self.index += 1,
            }
        }
        Err("unterminated GraphQL string literal".to_string())
    }

    fn skip_comment(&mut self) {
        while let Some(byte) = self.peek_byte() {
            self.index += 1;
            if byte == b'\n' || byte == b'\r' {
                return;
            }
        }
    }

    fn skip_ignored(&mut self) {
        loop {
            match self.peek_byte() {
                Some(b'#') => self.skip_comment(),
                Some(byte) if byte.is_ascii_whitespace() || byte == b',' => self.index += 1,
                _ => return,
            }
        }
    }

    fn consume_digits(&mut self) {
        while matches!(self.peek_byte(), Some(b'0'..=b'9')) {
            self.index += 1;
        }
    }

    fn expect_byte(&mut self, expected: u8) -> Result<(), String> {
        self.skip_ignored();
        match self.peek_byte() {
            Some(actual) if actual == expected => {
                self.index += 1;
                Ok(())
            }
            Some(actual) => Err(format!(
                "expected {}, found {}",
                expected as char, actual as char
            )),
            None => Err(format!("expected {}, found end of input", expected as char)),
        }
    }

    fn peek_byte(&self) -> Option<u8> {
        self.query.as_bytes().get(self.index).copied()
    }
}

fn is_graphql_name_start(character: char) -> bool {
    character.is_ascii_alphabetic() || character == '_'
}

fn is_graphql_name_continue(character: char) -> bool {
    character.is_ascii_alphanumeric() || character == '_'
}

fn is_graphql_name_start_byte(byte: u8) -> bool {
    is_graphql_name_start(byte as char)
}

fn is_graphql_name_continue_byte(byte: u8) -> bool {
    is_graphql_name_continue(byte as char)
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

    fn receive_bridge_request(engine: &SchemaEngine) -> Value {
        let request_json = engine
            .call_bridge
            .receive_json(&engine.runtime, Duration::from_millis(500))
            .unwrap()
            .expect("bridge request should arrive");
        decode(&request_json)
    }

    fn respond_bridge_request(engine: &SchemaEngine, request: &Value, response: Value) {
        let request_id = request["request_id"].as_str().unwrap();
        let response_json = serde_json::to_string(&response).unwrap();
        assert!(engine.call_bridge.respond(request_id, response_json));
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
    fn tool_field_arguments_support_inline_literals() {
        let engine =
            SchemaEngine::from_catalogue_json(Some(r#"[{"server": "demo", "name": "echo"}]"#))
                .unwrap();
        engine.attach_call_bridge();

        std::thread::scope(|scope| {
            let handle = scope.spawn(|| {
                decode(&engine.execute_json(
                    r#"{ demo_echo(text: "hi", count: 2, enabled: true, tags: ["a"], meta: {level: 3}) { isError text json } }"#,
                    Some("{}"),
                ))
            });

            let request = receive_bridge_request(&engine);
            assert_eq!(request["server"], "demo");
            assert_eq!(request["tool"], "echo");
            assert_eq!(request["operation"], "query");
            assert_eq!(
                request["arguments"],
                json!({
                    "text": "hi",
                    "count": 2,
                    "enabled": true,
                    "tags": ["a"],
                    "meta": {"level": 3},
                })
            );
            respond_bridge_request(
                &engine,
                &request,
                json!({"isError": false, "text": "ok", "json": request["arguments"].clone()}),
            );

            let response = handle.join().unwrap();
            assert_eq!(response["errors"], json!([]));
            assert_eq!(response["data"]["demo_echo"]["json"]["text"], "hi");
        });
    }

    #[test]
    fn tool_field_arguments_support_variables_and_aliases_concurrently() {
        let engine =
            SchemaEngine::from_catalogue_json(Some(r#"[{"server": "demo", "name": "echo"}]"#))
                .unwrap();
        engine.attach_call_bridge();

        std::thread::scope(|scope| {
            let handle = scope.spawn(|| {
                decode(&engine.execute_json(
                    r#"query Echo($text: String!, $meta: JSON) {
                        first: demo_echo(text: $text, meta: $meta) { isError text json }
                        second: demo_echo(text: "inline") { isError text json }
                    }"#,
                    Some(r#"{"text":"from-var","meta":{"source":"vars"}}"#),
                ))
            });

            let first = receive_bridge_request(&engine);
            let second = receive_bridge_request(&engine);
            assert_eq!(first["request_id"], "tool-call-1");
            assert_eq!(second["request_id"], "tool-call-2");
            assert_eq!(
                first["arguments"],
                json!({"text": "from-var", "meta": {"source": "vars"}})
            );
            assert_eq!(second["arguments"], json!({"text": "inline"}));

            respond_bridge_request(
                &engine,
                &second,
                json!({"isError": false, "text": "second", "json": second["arguments"].clone()}),
            );
            respond_bridge_request(
                &engine,
                &first,
                json!({"isError": false, "text": "first", "json": first["arguments"].clone()}),
            );

            let response = handle.join().unwrap();
            assert_eq!(response["errors"], json!([]));
            assert_eq!(response["data"]["first"]["text"], "first");
            assert_eq!(response["data"]["second"]["text"], "second");
        });
    }

    #[test]
    fn missing_referenced_variable_returns_graphql_error() {
        let engine =
            SchemaEngine::from_catalogue_json(Some(r#"[{"server": "demo", "name": "echo"}]"#))
                .unwrap();
        engine.attach_call_bridge();

        let response = decode(&engine.execute_json(
            "query Echo($text: String!) { demo_echo(text: $text) { json } }",
            Some("{}"),
        ));

        assert_eq!(response["data"], Value::Null);
        assert_eq!(
            response["errors"][0]["message"],
            "missing GraphQL variable: $text"
        );
        assert!(engine
            .call_bridge
            .receive_json(&engine.runtime, Duration::ZERO)
            .unwrap()
            .is_none());
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
