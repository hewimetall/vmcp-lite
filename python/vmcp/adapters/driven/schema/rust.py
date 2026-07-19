"""Rust-backed GraphQL schema engine adapter."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Protocol

from vmcp.domain.models import GraphQLError, GraphQLRequest, GraphQLResponse, ToolRegistry
from vmcp.domain.models.types import JsonValue
from vmcp.domain.ports import UpstreamPool


class _RustEngine(Protocol):
    def execute(self, query: str, variables_json: str | None = None) -> str:
        """Execute a query and return a JSON-encoded GraphQL response."""
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
        # The Rust stub does not consume upstreams until the ADR-0011 tokio bridge
        # is connected, but the port keeps the future callback surface explicit.
        _ = upstreams
        self._refresh_registry(registry)

        variables_json = json.dumps(
            dict(request.variables),
            separators=(",", ":"),
            sort_keys=True,
        )
        raw_response = self._engine.execute(request.query, variables_json)
        return _parse_graphql_response(raw_response)

    def _refresh_registry(self, registry: ToolRegistry) -> None:
        catalogue_json = build_tool_catalogue_json(registry)
        if catalogue_json == self._catalogue_json:
            return

        self._catalogue_json = catalogue_json
        self._engine = self._engine_factory(catalogue_json)


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


__all__ = [
    "RustEngineFactory",
    "RustSchemaEngine",
    "build_tool_catalogue_json",
]
