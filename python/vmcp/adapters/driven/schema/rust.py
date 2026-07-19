"""Rust-backed GraphQL schema engine adapter."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from collections.abc import Callable, Mapping
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


class _ToolCallerEngine(_RustEngine, Protocol):
    def set_tool_caller(self, call_tool: Callable[[str, str, str, str], str]) -> None:
        """Register the Python callback used by Rust tool resolvers."""
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
        self._tool_caller_callback: Callable[[str, str, str, str], str] = (
            self._call_tool_via_bridge
        )

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
        # Rust receives tool calls through the registered CallBridge callback; the
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
        """Attach the ADR-0011 bridge used by PyO3 tool resolver callbacks."""
        self._call_bridge = call_bridge
        self._bridge_loop = loop or asyncio.get_running_loop()
        self._attach_tool_caller()

    def _refresh_registry(self, registry: ToolRegistry) -> None:
        catalogue_json = build_tool_catalogue_json(registry)
        if catalogue_json == self._catalogue_json:
            return

        self._catalogue_json = catalogue_json
        self._engine = self._engine_factory(catalogue_json)
        self._attach_tool_caller()

    def _attach_tool_caller(self) -> None:
        if self._call_bridge is None:
            return

        set_tool_caller = getattr(self._engine, "set_tool_caller", None)
        if set_tool_caller is None:
            return

        set_tool_caller(self._tool_caller_callback)

    def _call_tool_via_bridge(
        self,
        server: str,
        tool: str,
        arguments_json: str,
        operation: str,
    ) -> str:
        bridge = self._call_bridge
        loop = self._bridge_loop
        if bridge is None or loop is None:
            return _encode_tool_result_error("CallBridge is not attached")

        arguments = _decode_tool_arguments(arguments_json)
        request = BridgeRequest(
            server=server,
            tool=tool,
            arguments=arguments,
            operation=operation,
        )

        try:
            future = asyncio.run_coroutine_threadsafe(bridge.request(request), loop)
            result = future.result()
        except concurrent.futures.CancelledError:
            return _encode_tool_result_error("CallBridge request was cancelled")
        except BaseException as exc:
            return _encode_tool_result_error(str(exc) or exc.__class__.__name__)

        return _encode_tool_result(result)


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


def _decode_tool_arguments(arguments_json: str) -> Mapping[str, JsonValue]:
    try:
        arguments = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        msg = f"tool resolver received invalid arguments JSON: {exc}"
        raise ValueError(msg) from exc

    if not isinstance(arguments, dict):
        msg = "tool resolver arguments must be a JSON object"
        raise ValueError(msg)
    return arguments


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
