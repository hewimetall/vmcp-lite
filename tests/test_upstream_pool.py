"""Tests for the stdio UpstreamPool driven adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from vmcp.adapters.driven.upstream import (
    StdioUpstreamConfig,
    StdioUpstreamPool,
    UpstreamUnavailableError,
)
from vmcp.domain.models import JsonValue, ServerId, ToolCall


class FakeProcess:
    def __init__(self, *, exits_after_terminate: bool = True) -> None:
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self._exits_after_terminate = exits_after_terminate
        self._killed = asyncio.Event()

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self._exits_after_terminate:
            self.returncode = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self._killed.set()

    async def wait(self) -> int:
        if self.returncode is None:
            await self._killed.wait()
        return self.returncode or 0


class FakeClient:
    def __init__(
        self,
        server_id: str,
        *,
        process: FakeProcess | None = None,
        result: Any | None = None,
        delay: float = 0,
        counter: list[int] | None = None,
    ) -> None:
        self.server_id = server_id
        self.process = process
        self.result = result
        self.delay = delay
        self.counter = counter
        self.active_calls = 0
        self.max_active_calls = 0
        self.calls: list[tuple[str, Mapping[str, JsonValue]]] = []
        self.closed = False

    async def call_tool(self, tool_name: str, arguments: Mapping[str, JsonValue]) -> Any:
        self.calls.append((tool_name, arguments))
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        if self.counter is not None:
            self.counter[0] += 1
            self.counter[1] = max(self.counter[1], self.counter[0])
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.result is not None:
                return self.result
            return {
                "content": {
                    "server": self.server_id,
                    "tool": tool_name,
                    "arguments": arguments,
                }
            }
        finally:
            self.active_calls -= 1
            if self.counter is not None:
                self.counter[0] -= 1

    async def close(self) -> None:
        self.closed = True


def _call(server: str, tool: str = "read", **arguments: JsonValue) -> ToolCall:
    return ToolCall(
        server_id=ServerId(value=server),
        tool_name=tool,
        arguments=arguments,
    )


def test_start_collects_spawn_failures_and_keeps_partial_pool() -> None:
    clients: dict[str, FakeClient] = {}

    async def client_factory(config: StdioUpstreamConfig) -> FakeClient:
        server = config.server_id.value
        if server == "bad":
            raise RuntimeError("boom")
        client = FakeClient(server)
        clients[server] = client
        return client

    async def scenario() -> None:
        pool = StdioUpstreamPool(
            [
                StdioUpstreamConfig(server_id="good", command="fake-good"),
                StdioUpstreamConfig(server_id="bad", command="fake-bad"),
            ],
            client_factory=client_factory,
        )

        await pool.start()

        assert [server.value for server in pool.active_server_ids] == ["good"]
        assert len(pool.spawn_failures) == 1
        assert pool.spawn_failures[0].server_id == ServerId(value="bad")

        result = await pool.caller_for(ServerId(value="good")).call_tool(_call("good"))
        assert result.content == {"server": "good", "tool": "read", "arguments": {}}

        with pytest.raises(UpstreamUnavailableError, match="failed to start"):
            pool.caller_for(ServerId(value="bad"))

    asyncio.run(scenario())


def test_same_server_calls_are_serialized_by_call_lock() -> None:
    client = FakeClient("alpha", delay=0.01)

    async def client_factory(config: StdioUpstreamConfig) -> FakeClient:
        _ = config
        return client

    async def scenario() -> None:
        pool = StdioUpstreamPool(
            [StdioUpstreamConfig(server_id="alpha", command="fake-alpha")],
            client_factory=client_factory,
        )
        await pool.start()

        caller = pool.caller_for(ServerId(value="alpha"))
        results = await asyncio.gather(
            caller.call_tool(_call("alpha", value=1)),
            caller.call_tool(_call("alpha", value=2)),
        )

        assert [result.content["arguments"]["value"] for result in results] == [1, 2]
        assert client.max_active_calls == 1

    asyncio.run(scenario())


def test_different_server_calls_can_run_in_parallel() -> None:
    global_counter = [0, 0]
    clients: dict[str, FakeClient] = {}

    async def client_factory(config: StdioUpstreamConfig) -> FakeClient:
        client = FakeClient(config.server_id.value, delay=0.02, counter=global_counter)
        clients[config.server_id.value] = client
        return client

    async def scenario() -> None:
        pool = StdioUpstreamPool(
            [
                StdioUpstreamConfig(server_id="alpha", command="fake-alpha"),
                StdioUpstreamConfig(server_id="beta", command="fake-beta"),
            ],
            client_factory=client_factory,
        )
        await pool.start()

        alpha = pool.caller_for(ServerId(value="alpha"))
        beta = pool.caller_for(ServerId(value="beta"))
        results = await asyncio.gather(
            alpha.call_tool(_call("alpha", value=1)),
            beta.call_tool(_call("beta", value=2)),
        )

        assert {result.server_id.value for result in results} == {"alpha", "beta"}
        assert global_counter[1] == 2
        assert clients["alpha"].max_active_calls == 1
        assert clients["beta"].max_active_calls == 1

    asyncio.run(scenario())


def test_mcp_tool_error_maps_to_domain_tool_result() -> None:
    client = FakeClient(
        "alpha",
        result={"content": [{"type": "text", "text": "upstream exploded"}], "isError": True},
    )

    async def client_factory(config: StdioUpstreamConfig) -> FakeClient:
        _ = config
        return client

    async def scenario() -> None:
        pool = StdioUpstreamPool(
            [StdioUpstreamConfig(server_id="alpha", command="fake-alpha")],
            client_factory=client_factory,
        )
        await pool.start()

        result = await pool.caller_for(ServerId(value="alpha")).call_tool(_call("alpha"))

        assert result.is_error is True
        assert result.content == [{"type": "text", "text": "upstream exploded"}]
        assert result.error_message == "upstream exploded"

    asyncio.run(scenario())


def test_stop_closes_clients_and_reaps_children_with_kill_escalation() -> None:
    process = FakeProcess(exits_after_terminate=False)
    client = FakeClient("alpha", process=process)

    async def client_factory(config: StdioUpstreamConfig) -> FakeClient:
        _ = config
        return client

    async def scenario() -> None:
        pool = StdioUpstreamPool(
            [StdioUpstreamConfig(server_id="alpha", command="fake-alpha")],
            client_factory=client_factory,
            shutdown_timeout=0.001,
        )
        await pool.start()

        await pool.stop()

        assert client.closed is True
        assert process.terminate_calls == 1
        assert process.kill_calls == 1
        assert process.returncode == -9
        with pytest.raises(UpstreamUnavailableError):
            pool.caller_for(ServerId(value="alpha"))

    asyncio.run(scenario())
