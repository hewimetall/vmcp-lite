"""Tests for the ADR-0011 asyncio CallBridge adapter."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from vmcp.adapters.bridge.call_bridge import BridgeRequest, CallBridge


async def _cancel_task(task: asyncio.Task[Any]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_parallel_concurrent_requests_resolve_independently() -> None:
    async def scenario() -> None:
        bridge = CallBridge()

        first = asyncio.create_task(bridge.call("alpha", "read", {"value": 1}))
        second = asyncio.create_task(bridge.call("beta", "read", {"value": 2}))

        first_pending = await asyncio.wait_for(bridge.receive(), timeout=0.1)
        second_pending = await asyncio.wait_for(bridge.receive(), timeout=0.1)

        assert first_pending.request.server == "alpha"
        assert second_pending.request.server == "beta"

        assert second_pending.respond({"ok": 2}) is True
        assert first_pending.respond({"ok": 1}) is True

        assert await asyncio.wait_for(first, timeout=0.1) == {"ok": 1}
        assert await asyncio.wait_for(second, timeout=0.1) == {"ok": 2}

    asyncio.run(scenario())


def test_no_deadlock_under_many_concurrent_query_calls() -> None:
    class RecordingCaller:
        def __init__(self) -> None:
            self.in_flight = 0
            self.max_in_flight = 0

        async def call_tool(self, request: BridgeRequest) -> dict[str, object]:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                await asyncio.sleep(0.01)
                return {
                    "server": request.server,
                    "tool": request.tool,
                    "value": request.arguments["value"],
                }
            finally:
                self.in_flight -= 1

    async def scenario() -> None:
        bridge = CallBridge()
        caller = RecordingCaller()
        worker = asyncio.create_task(bridge.serve(caller))

        try:
            calls = [
                bridge.call(f"server-{index}", "read", {"value": index}, operation="query")
                for index in range(32)
            ]
            results = await asyncio.wait_for(asyncio.gather(*calls), timeout=1)
        finally:
            await _cancel_task(worker)

        assert [result["value"] for result in results] == list(range(32))
        assert caller.max_in_flight > 1

    asyncio.run(scenario())


def test_timeout_cancels_waiting_future_without_accepting_late_response() -> None:
    async def scenario() -> None:
        bridge = CallBridge()

        call = asyncio.create_task(bridge.call("slow", "read", timeout=0.01))
        pending = await asyncio.wait_for(bridge.receive(), timeout=0.1)

        with pytest.raises(asyncio.TimeoutError):
            await call

        assert pending.done is True
        assert pending.respond({"too": "late"}) is False

    asyncio.run(scenario())


def test_mutations_are_serialized_but_do_not_block_queries() -> None:
    class MixedCaller:
        def __init__(self) -> None:
            self.active_mutations = 0
            self.max_active_mutations = 0
            self.mutation_started = asyncio.Event()
            self.query_started = asyncio.Event()
            self.release_mutation = asyncio.Event()

        async def call_tool(self, request: BridgeRequest) -> str:
            if request.operation == "mutation":
                self.active_mutations += 1
                self.max_active_mutations = max(
                    self.max_active_mutations,
                    self.active_mutations,
                )
                self.mutation_started.set()
                try:
                    await self.release_mutation.wait()
                    return f"mutation:{request.arguments['value']}"
                finally:
                    self.active_mutations -= 1

            self.query_started.set()
            return f"query:{request.arguments['value']}"

    async def scenario() -> None:
        bridge = CallBridge()
        caller = MixedCaller()
        worker = asyncio.create_task(bridge.serve(caller))

        try:
            first_mutation = asyncio.create_task(
                bridge.call("state", "write", {"value": 1}, operation="mutation")
            )
            assert await asyncio.wait_for(caller.mutation_started.wait(), timeout=0.1) is True

            second_mutation = asyncio.create_task(
                bridge.call("state", "write", {"value": 2}, operation="mutation")
            )
            query = asyncio.create_task(
                bridge.call("state", "read", {"value": 3}, operation="query")
            )

            assert await asyncio.wait_for(caller.query_started.wait(), timeout=0.1) is True
            assert await asyncio.wait_for(query, timeout=0.1) == "query:3"
            assert caller.max_active_mutations == 1

            caller.release_mutation.set()
            assert await asyncio.wait_for(first_mutation, timeout=0.1) == "mutation:1"
            assert await asyncio.wait_for(second_mutation, timeout=0.1) == "mutation:2"
        finally:
            await _cancel_task(worker)

    asyncio.run(scenario())
