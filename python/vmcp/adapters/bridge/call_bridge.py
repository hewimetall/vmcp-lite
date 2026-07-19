"""ADR-0011 asyncio CallBridge adapter.

The Rust/PyO3 GraphQL worker will fan out GraphQL ``Query`` fields on a tokio
runtime and ask Python to call upstream MCP tools. This adapter is the Python
side of that boundary: every submitted tool call is placed on an asyncio request
queue and carries a private Future that acts like ADR-0011's oneshot response.
The worker side receives :class:`PendingBridgeCall` values and completes exactly
the matching Future via ``respond``/``fail``.

The contract is intentionally adapter-local to preserve HEX boundaries. Domain
ports describe *what* the application needs; this module describes *how* the
Python event loop exchanges calls with a worker runtime. Query calls are
dispatched without holding a serial lock so same-process fan-out cannot deadlock.
Mutation calls share a lock in ``serve`` so state-changing work queues
sequentially while unrelated Query calls may continue concurrently.

The final PyO3 wiring can either drive ``receive`` directly from Rust callbacks
or use ``serve`` with a Python ``ToolCaller`` implementation while Rust owns the
GraphQL execution runtime.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol, cast

Operation = Literal["query", "mutation"]

_OPERATIONS: frozenset[str] = frozenset(("query", "mutation"))


def _normalize_operation(operation: str) -> Operation:
    normalized = operation.lower()
    if normalized not in _OPERATIONS:
        msg = f"operation must be 'query' or 'mutation', got {operation!r}"
        raise ValueError(msg)
    return cast(Operation, normalized)


def _freeze_mapping(values: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(values or {}))


@dataclass(frozen=True, slots=True)
class BridgeRequest:
    """A tool-call request crossing the ADR-0011 bridge boundary."""

    server: str
    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    operation: Operation = "query"
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.server:
            raise ValueError("server must not be empty")
        if not self.tool:
            raise ValueError("tool must not be empty")

        object.__setattr__(self, "operation", _normalize_operation(self.operation))
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


class ToolCaller(Protocol):
    """Callback protocol used by ``CallBridge.serve`` to invoke Python tools."""

    def call_tool(self, request: BridgeRequest) -> Awaitable[Any] | Any:
        """Call the upstream tool described by ``request``."""


@dataclass(slots=True)
class PendingBridgeCall:
    """Worker-side request handle with a oneshot-style response channel."""

    request: BridgeRequest
    _future: asyncio.Future[Any]

    @property
    def done(self) -> bool:
        """Return whether the waiting Python caller can still accept a response."""
        return self._future.done()

    def respond(self, value: Any) -> bool:
        """Resolve the caller Future with ``value``.

        Returns ``False`` when the caller already timed out or cancelled, matching
        the late oneshot-send failure shape Rust code will see.
        """
        if self._future.done():
            return False
        self._future.set_result(value)
        return True

    def fail(self, error: BaseException) -> bool:
        """Reject the caller Future with ``error`` if it is still waiting."""
        if self._future.done():
            return False
        self._future.set_exception(error)
        return True

    def cancel(self) -> bool:
        """Cancel the caller Future if it is still waiting."""
        if self._future.done():
            return False
        return self._future.cancel()


class CallBridge:
    """Async request queue plus per-call Future response bridge."""

    def __init__(self, *, max_queue_size: int = 0) -> None:
        self._queue: asyncio.Queue[PendingBridgeCall] = asyncio.Queue(maxsize=max_queue_size)
        self._mutation_lock = asyncio.Lock()
        self._dispatch_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    async def call(
        self,
        server: str,
        tool: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        operation: str = "query",
        timeout: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Any:
        """Submit a tool call and await its matching response Future."""
        request = BridgeRequest(
            server=server,
            tool=tool,
            arguments=_freeze_mapping(arguments),
            operation=_normalize_operation(operation),
            metadata=_freeze_mapping(metadata),
        )
        return await self.request(request, timeout=timeout)

    async def request(self, request: BridgeRequest, *, timeout: float | None = None) -> Any:
        """Enqueue ``request`` and wait for the worker to complete its oneshot."""
        if self._closed:
            raise RuntimeError("CallBridge is closed")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        pending = PendingBridgeCall(request=request, _future=future)
        await self._queue.put(pending)

        try:
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            pending.cancel()
            raise
        except asyncio.CancelledError:
            pending.cancel()
            raise

    async def receive(self) -> PendingBridgeCall:
        """Receive the next pending call for Rust/PyO3 or a Python worker loop."""
        if self._closed and self._queue.empty():
            raise RuntimeError("CallBridge is closed")
        return await self._queue.get()

    async def serve(self, caller: ToolCaller) -> None:
        """Continuously dispatch bridge requests to ``caller.call_tool``.

        Query requests are scheduled as independent asyncio tasks. Mutation
        requests are also scheduled independently, but they acquire a shared lock
        before invoking the caller so mutations complete serially.
        """
        try:
            while True:
                pending = await self.receive()
                task = asyncio.create_task(self._dispatch(caller, pending))
                self._dispatch_tasks.add(task)
                task.add_done_callback(self._dispatch_tasks.discard)
        except asyncio.CancelledError:
            await self._cancel_dispatch_tasks()
            raise

    def close(self) -> None:
        """Close the bridge and cancel queued requests that have not dispatched."""
        self._closed = True
        while True:
            try:
                pending = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            pending.cancel()

    async def _dispatch(self, caller: ToolCaller, pending: PendingBridgeCall) -> None:
        try:
            if pending.request.operation == "mutation":
                async with self._mutation_lock:
                    await self._call_and_respond(caller, pending)
                return

            await self._call_and_respond(caller, pending)
        except asyncio.CancelledError:
            pending.cancel()
            raise
        except BaseException as exc:
            pending.fail(exc)

    async def _call_and_respond(self, caller: ToolCaller, pending: PendingBridgeCall) -> None:
        if pending.done:
            return

        result = caller.call_tool(pending.request)
        if inspect.isawaitable(result):
            result = await result
        pending.respond(result)

    async def _cancel_dispatch_tasks(self) -> None:
        tasks = tuple(self._dispatch_tasks)
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
