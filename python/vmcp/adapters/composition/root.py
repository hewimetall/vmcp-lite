"""Composition root for vmcp-lite adapters."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from vmcp.adapters.bridge.call_bridge import BridgeRequest, CallBridge
from vmcp.adapters.driven.registry import (
    SidecarRegistryLoader,
)
from vmcp.adapters.driven.registry import (
    StdioUpstreamConfig as RegistryStdioUpstreamConfig,
)
from vmcp.adapters.driven.schema import RustEngineFactory, RustSchemaEngine
from vmcp.adapters.driven.upstream import (
    StdioUpstreamConfig as PoolStdioUpstreamConfig,
)
from vmcp.adapters.driven.upstream import (
    StdioUpstreamPool,
)
from vmcp.domain.models import (
    GraphQLError,
    GraphQLRequest,
    GraphQLResponse,
    JsonValue,
    ServerId,
    ToolCall,
    ToolRegistry,
    ToolResult,
)
from vmcp.domain.ports import RegistryLoader, SchemaEngine, ToolCaller, UpstreamPool
from vmcp.domain.usecases import ExecuteQueryGraphQL, compose_usecases

UpstreamPoolFactory = Callable[[Sequence[PoolStdioUpstreamConfig]], UpstreamPool]
SchemaEngineBuilder = Callable[[ToolRegistry, Mapping[str, str | None]], SchemaEngine]


@runtime_checkable
class CallBridgeAttachable(Protocol):
    """Schema adapter extension point for ADR-0011 CallBridge callbacks."""

    def set_call_bridge(
        self,
        call_bridge: CallBridge,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Attach the active CallBridge to the schema engine."""


class EmptyRegistryLoader:
    """Registry loader stub until stdio discovery is implemented."""

    async def load_registry(self) -> ToolRegistry:
        # TODO(ADR-0006): implement discovery ladder and registry parsing.
        return ToolRegistry()


class LoadedRegistryLoader:
    """Registry loader for a boot-time snapshot used by a configured root."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def load_registry(self) -> ToolRegistry:
        return self._registry


class StubUpstreamPool:
    """Upstream pool stub; CallBridge-backed implementation is owned elsewhere."""

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def caller_for(self, server_id: ServerId) -> ToolCaller:
        raise NotImplementedError(f"upstream pool is not wired for {server_id.value!r}")


class StubSchemaEngine:
    """Schema engine stub until the Rust/PyO3 boundary is wired."""

    async def execute(
        self,
        request: GraphQLRequest,
        registry: ToolRegistry,
        upstreams: UpstreamPool,
    ) -> GraphQLResponse:
        _ = (request, upstreams)
        return GraphQLResponse(
            errors=(
                GraphQLError(
                    message="query_graphql schema engine is not wired yet",
                    extensions={
                        "code": "schema_engine_not_wired",
                        "registry_tool_count": len(registry.tools),
                    },
                ),
            ),
        )


class UpstreamPoolBridgeCaller:
    """CallBridge caller that delegates requests into the configured upstream pool."""

    def __init__(self, upstreams: UpstreamPool) -> None:
        self._upstreams = upstreams

    async def call_tool(self, request: BridgeRequest) -> ToolResult:
        call = ToolCall(
            server_id=ServerId(value=request.server),
            tool_name=request.tool,
            arguments=cast(Mapping[str, JsonValue], request.arguments),
        )
        caller = self._upstreams.caller_for(call.server_id)
        return await caller.call_tool(call)


@dataclass(frozen=True, slots=True)
class CompositionRoot:
    """Container for vmcp-lite dependencies."""

    execute_query_graphql: ExecuteQueryGraphQL
    configured: bool = False
    registry_loader: RegistryLoader | None = None
    upstreams: UpstreamPool | None = None
    schema_engine: SchemaEngine | None = None
    call_bridge: CallBridge | None = None
    call_bridge_task: asyncio.Task[None] | None = None

    async def stop(self) -> None:
        """Stop background bridge work and upstream resources."""
        if self.schema_engine is not None:
            close_call_bridge = getattr(self.schema_engine, "close_call_bridge", None)
            if close_call_bridge is not None:
                result = close_call_bridge()
                if inspect.isawaitable(result):
                    await result

        if self.call_bridge is not None:
            self.call_bridge.close()

        if self.call_bridge_task is not None:
            self.call_bridge_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.call_bridge_task

        if self.upstreams is not None:
            await self.upstreams.stop()


def build_composition_root(
    *,
    config_path: str | Path | None = None,
    registry_loader: RegistryLoader | None = None,
    upstreams: UpstreamPool | None = None,
    schema_engine: SchemaEngine | None = None,
) -> CompositionRoot:
    """Build the vmcp-lite composition root from injected ports or stubs."""
    resolved_registry_loader = registry_loader or (
        SidecarRegistryLoader.from_config_file(config_path)
        if config_path is not None
        else EmptyRegistryLoader()
    )
    resolved_upstreams = upstreams or StubUpstreamPool()
    resolved_schema_engine = schema_engine or StubSchemaEngine()

    return CompositionRoot(
        execute_query_graphql=compose_usecases(
            registry_loader=resolved_registry_loader,
            upstreams=resolved_upstreams,
            schema_engine=resolved_schema_engine,
        ),
        configured=all(
            (
                registry_loader is not None or config_path is not None,
                upstreams is not None,
                schema_engine is not None,
            )
        ),
        registry_loader=resolved_registry_loader,
        upstreams=resolved_upstreams,
        schema_engine=resolved_schema_engine,
    )


async def boot_composition_root(
    *,
    config_path: str | Path | None = None,
    registry_loader: RegistryLoader | None = None,
    upstreams: UpstreamPool | None = None,
    schema_engine: SchemaEngine | None = None,
    upstream_pool_factory: UpstreamPoolFactory | None = None,
    schema_engine_builder: SchemaEngineBuilder | None = None,
    rust_engine_factory: RustEngineFactory | None = None,
    enable_call_bridge: bool = True,
) -> CompositionRoot:
    """Boot the configured adapter graph and start owned upstream resources."""
    resolved_registry_loader = registry_loader or (
        SidecarRegistryLoader.from_config_file(config_path)
        if config_path is not None
        else EmptyRegistryLoader()
    )
    registry = await resolved_registry_loader.load_registry()
    upstream_descriptions = _upstream_descriptions(resolved_registry_loader)

    resolved_upstreams = upstreams or _build_stdio_pool(
        resolved_registry_loader,
        upstream_pool_factory=upstream_pool_factory,
    )
    resolved_schema_engine = schema_engine or _build_schema_engine(
        registry,
        upstream_descriptions=upstream_descriptions,
        schema_engine_builder=schema_engine_builder,
        rust_engine_factory=rust_engine_factory,
    )

    call_bridge: CallBridge | None = None
    call_bridge_task: asyncio.Task[None] | None = None
    try:
        await resolved_upstreams.start()
        if enable_call_bridge:
            call_bridge = CallBridge()
            _attach_call_bridge(resolved_schema_engine, call_bridge)
            call_bridge_task = asyncio.create_task(
                call_bridge.serve(UpstreamPoolBridgeCaller(resolved_upstreams))
            )
    except BaseException:
        if call_bridge is not None:
            call_bridge.close()
        if call_bridge_task is not None:
            call_bridge_task.cancel()
            with suppress(asyncio.CancelledError):
                await call_bridge_task
        await resolved_upstreams.stop()
        raise

    snapshot_loader = LoadedRegistryLoader(registry)
    return CompositionRoot(
        execute_query_graphql=compose_usecases(
            registry_loader=snapshot_loader,
            upstreams=resolved_upstreams,
            schema_engine=resolved_schema_engine,
        ),
        configured=config_path is not None
        or registry_loader is not None
        or upstreams is not None
        or schema_engine is not None,
        registry_loader=snapshot_loader,
        upstreams=resolved_upstreams,
        schema_engine=resolved_schema_engine,
        call_bridge=call_bridge,
        call_bridge_task=call_bridge_task,
    )


def _build_schema_engine(
    registry: ToolRegistry,
    *,
    upstream_descriptions: Mapping[str, str | None],
    schema_engine_builder: SchemaEngineBuilder | None,
    rust_engine_factory: RustEngineFactory | None,
) -> SchemaEngine:
    if schema_engine_builder is not None:
        return schema_engine_builder(registry, upstream_descriptions)

    return RustSchemaEngine.from_registry(
        registry,
        upstream_descriptions=upstream_descriptions,
        engine_factory=rust_engine_factory,
    )


def _build_stdio_pool(
    registry_loader: RegistryLoader,
    *,
    upstream_pool_factory: UpstreamPoolFactory | None,
) -> UpstreamPool:
    pool_factory = upstream_pool_factory or StdioUpstreamPool
    configs: Sequence[PoolStdioUpstreamConfig] = ()
    if isinstance(registry_loader, SidecarRegistryLoader):
        configs = _stdio_pool_configs(registry_loader.upstreams)
    return pool_factory(configs)


def _stdio_pool_configs(
    upstreams: Sequence[RegistryStdioUpstreamConfig],
) -> tuple[PoolStdioUpstreamConfig, ...]:
    return tuple(
        PoolStdioUpstreamConfig(
            server_id=upstream.name,
            command=upstream.command,
            args=upstream.args,
            env=upstream.env,
            cwd=upstream.cwd,
        )
        for upstream in upstreams
    )


def _upstream_descriptions(registry_loader: RegistryLoader) -> dict[str, str | None]:
    if not isinstance(registry_loader, SidecarRegistryLoader):
        return {}
    return {upstream.name: upstream.description for upstream in registry_loader.upstreams}


def _attach_call_bridge(schema_engine: SchemaEngine, call_bridge: CallBridge) -> None:
    if isinstance(schema_engine, CallBridgeAttachable):
        schema_engine.set_call_bridge(call_bridge, loop=asyncio.get_running_loop())


__all__ = [
    "CallBridgeAttachable",
    "CompositionRoot",
    "EmptyRegistryLoader",
    "LoadedRegistryLoader",
    "SchemaEngineBuilder",
    "StubSchemaEngine",
    "StubUpstreamPool",
    "UpstreamPoolBridgeCaller",
    "UpstreamPoolFactory",
    "boot_composition_root",
    "build_composition_root",
]
