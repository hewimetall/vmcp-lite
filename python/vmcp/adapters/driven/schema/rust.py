"""Rust-backed GraphQL schema engine adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import Any, Protocol

from vmcp.adapters.bridge.call_bridge import BridgeRequest, CallBridge
from vmcp.domain.models import (
    GraphQLError,
    GraphQLRequest,
    GraphQLResponse,
    ToolRegistry,
    ToolResult,
)
from vmcp.domain.models.types import JsonValue
from vmcp.domain.ports import UpstreamPool


class _RustEngine(Protocol):
    def execute(self, query: str, variables_json: str | None = None) -> str:
        """Execute a query and return a JSON-encoded GraphQL response."""
        ...


class _BridgeRustEngine(_RustEngine, Protocol):
    def attach_call_bridge(self) -> None:
        """Enable Rust's ADR-0011 request channel for tool resolvers."""
        ...

    def detach_call_bridge(self) -> None:
        """Disable Rust's ADR-0011 request channel and cancel pending calls."""
        ...

    def receive_tool_call(self, timeout_ms: int = 100) -> str | None:
        """Receive the next Rust-originated tool request as JSON."""
        ...

    def respond_tool_call(self, request_id: str, result_json: str) -> bool:
        """Complete a Rust-originated tool request."""
        ...

    def fail_tool_call(self, request_id: str, message: str) -> bool:
        """Fail a Rust-originated tool request."""
        ...


RustEngineFactory = Callable[[str], _RustEngine]


class RustSchemaEngine:
    """Domain ``SchemaEngine`` backed by ``vmcp._graphql.SchemaEngine``."""

    def __init__(
        self,
        catalogue_json: str | None = None,
        *,
        engine_factory: RustEngineFactory | None = None,
    ) -> None:
        self._engine_factory = engine_factory or _default_engine_factory
        self._catalogue_json = catalogue_json or "[]"
        self._engine = self._engine_factory(self._catalogue_json)
        self._call_bridge: CallBridge | None = None
        self._bridge_loop: asyncio.AbstractEventLoop | None = None
        self._rust_bridge_task: asyncio.Task[None] | None = None
        self._rust_response_tasks: set[asyncio.Task[None]] = set()
        self._rust_bridge_engine: _RustEngine | None = None

    @classmethod
    def from_registry(
        cls,
        registry: ToolRegistry,
        *,
        upstream_descriptions: Mapping[str, str | None] | None = None,
        engine_factory: RustEngineFactory | None = None,
    ) -> RustSchemaEngine:
        """Build an engine from a registry snapshot."""
        return cls(
            build_tool_catalogue_json(
                registry,
                upstream_descriptions=upstream_descriptions,
            ),
            engine_factory=engine_factory,
        )

    async def execute(
        self,
        request: GraphQLRequest,
        registry: ToolRegistry,
        upstreams: UpstreamPool,
    ) -> GraphQLResponse:
        """Execute a GraphQL request through the Rust extension."""
        # Rust receives tool calls through its ADR-0011 request channel; the
        # upstreams port remains part of the domain contract for refreshes/tests.
        _ = upstreams
        self._refresh_registry(registry)

        variables_json = json.dumps(
            dict(request.variables),
            separators=(",", ":"),
            sort_keys=True,
        )
        raw_response = await asyncio.to_thread(self._engine.execute, request.query, variables_json)
        return _parse_graphql_response(raw_response)

    def set_call_bridge(
        self,
        call_bridge: CallBridge,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Attach an asyncio CallBridge to Rust's ADR-0011 request channel."""
        self._call_bridge = call_bridge
        self._bridge_loop = loop or asyncio.get_running_loop()
        self._attach_tool_caller()

    async def close_call_bridge(self) -> None:
        """Detach Rust's bridge and stop the Python drain task."""
        engine = self._rust_bridge_engine
        if _is_bridge_engine(engine):
            engine.detach_call_bridge()

        task = self._rust_bridge_task
        self._rust_bridge_task = None
        self._rust_bridge_engine = None
        self._call_bridge = None
        self._bridge_loop = None

        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        response_tasks = tuple(self._rust_response_tasks)
        for response_task in response_tasks:
            response_task.cancel()
        if response_tasks:
            await asyncio.gather(*response_tasks, return_exceptions=True)
        self._rust_response_tasks.clear()

    def _refresh_registry(self, registry: ToolRegistry) -> None:
        catalogue_json = build_tool_catalogue_json(registry)
        if catalogue_json == self._catalogue_json:
            return

        self._catalogue_json = catalogue_json
        if _is_bridge_engine(self._engine):
            self._engine.detach_call_bridge()
        self._engine = self._engine_factory(catalogue_json)
        self._attach_tool_caller()

    def _attach_tool_caller(self) -> None:
        bridge = self._call_bridge
        loop = self._bridge_loop
        if bridge is None or loop is None or not _is_bridge_engine(self._engine):
            return

        self._engine.attach_call_bridge()
        self._start_rust_bridge_worker(self._engine, bridge, loop)

    def _start_rust_bridge_worker(
        self,
        engine: _BridgeRustEngine,
        bridge: CallBridge,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        if (
            self._rust_bridge_task is not None
            and not self._rust_bridge_task.done()
            and self._rust_bridge_engine is engine
        ):
            return

        if self._rust_bridge_task is not None and not self._rust_bridge_task.done():
            self._rust_bridge_task.cancel()

        self._rust_bridge_engine = engine
        self._rust_bridge_task = loop.create_task(self._drain_rust_bridge(engine, bridge))

    async def _drain_rust_bridge(
        self,
        engine: _BridgeRustEngine,
        bridge: CallBridge,
    ) -> None:
        while True:
            request_json = await asyncio.to_thread(engine.receive_tool_call, 100)
            if request_json is None:
                continue

            request = _decode_bridge_request(request_json)
            task = asyncio.create_task(self._complete_rust_request(engine, bridge, request))
            self._rust_response_tasks.add(task)
            task.add_done_callback(self._rust_response_tasks.discard)

    async def _complete_rust_request(
        self,
        engine: _BridgeRustEngine,
        bridge: CallBridge,
        request: BridgeRequest,
    ) -> None:
        try:
            result = await bridge.request(request)
        except asyncio.CancelledError:
            engine.fail_tool_call(request.request_id, "CallBridge request was cancelled")
            raise
        except BaseException as exc:
            engine.fail_tool_call(request.request_id, str(exc) or exc.__class__.__name__)
        else:
            engine.respond_tool_call(request.request_id, _encode_tool_result(result))


def build_tool_catalogue_json(
    registry: ToolRegistry,
    *,
    upstream_descriptions: Mapping[str, str | None] | None = None,
) -> str:
    """Serialize domain tool metadata into the Rust SchemaEngine catalogue shape."""
    descriptions = dict(upstream_descriptions or {})
    catalogue: list[dict[str, JsonValue]] = []
    for tool in registry.tools:
        server_id = tool.server_id.value
        item: dict[str, JsonValue] = {
            "server": server_id,
            "name": tool.name,
            "description": tool.description,
            "read_only": tool.read_only,
            "input_schema": dict(tool.input_schema),
        }
        server_description = descriptions.get(server_id)
        if server_description:
            item["server_description"] = server_description
        catalogue.append(item)

    return json.dumps(catalogue, separators=(",", ":"), sort_keys=True)


def _default_engine_factory(catalogue_json: str) -> _RustEngine:
    try:
        from vmcp._graphql import SchemaEngine as PyO3SchemaEngine
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on build environment.
        raise RuntimeError(
            "vmcp._graphql extension is not built; install vmcp-lite with maturin first"
        ) from exc

    if hasattr(PyO3SchemaEngine, "build"):
        return PyO3SchemaEngine.build(catalogue_json)
    return PyO3SchemaEngine(catalogue_json)


def _parse_graphql_response(raw_response: str) -> GraphQLResponse:
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        return GraphQLResponse(
            errors=(
                GraphQLError(
                    message=f"schema engine returned invalid JSON: {exc}",
                    extensions={"code": "schema_engine_invalid_response"},
                ),
            ),
        )

    if not isinstance(payload, dict):
        return GraphQLResponse(
            errors=(
                GraphQLError(
                    message="schema engine response must be a JSON object",
                    extensions={"code": "schema_engine_invalid_response"},
                ),
            ),
        )
    return GraphQLResponse.model_validate(payload)


def _is_bridge_engine(engine: _RustEngine | None) -> bool:
    return engine is not None and all(
        callable(getattr(engine, name, None))
        for name in (
            "attach_call_bridge",
            "detach_call_bridge",
            "receive_tool_call",
            "respond_tool_call",
            "fail_tool_call",
        )
    )


def _decode_bridge_request(request_json: str) -> BridgeRequest:
    try:
        payload = json.loads(request_json)
    except json.JSONDecodeError as exc:
        msg = f"Rust bridge emitted invalid request JSON: {exc}"
        raise ValueError(msg) from exc

    if not isinstance(payload, dict):
        msg = "Rust bridge request must be a JSON object"
        raise ValueError(msg)

    arguments = payload.get("arguments", {})
    if not isinstance(arguments, dict):
        msg = "Rust bridge request arguments must be a JSON object"
        raise ValueError(msg)
    return BridgeRequest(
        server=_required_string(payload, "server"),
        tool=_required_string(payload, "tool"),
        arguments=arguments,
        operation=_required_string(payload, "operation"),
        request_id=_required_string(payload, "request_id"),
    )


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    msg = f"Rust bridge request field {key!r} must be a non-empty string"
    raise ValueError(msg)


def _encode_tool_result(result: Any) -> str:
    if isinstance(result, ToolResult):
        payload: dict[str, JsonValue] = {
            "isError": result.is_error,
            "text": result.error_message if result.is_error else _content_text(result.content),
            "json": result.content,
        }
        return _json_dumps(payload)

    if isinstance(result, Mapping) and {"isError", "text", "json"}.issubset(result.keys()):
        return _json_dumps(dict(result))

    payload = {
        "isError": False,
        "text": _content_text(result),
        "json": result,
    }
    return _json_dumps(payload)


def _encode_tool_result_error(message: str) -> str:
    return _json_dumps({"isError": True, "text": message, "json": None})


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return _json_dumps(content)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


__all__ = [
    "RustEngineFactory",
    "RustSchemaEngine",
    "build_tool_catalogue_json",
]
