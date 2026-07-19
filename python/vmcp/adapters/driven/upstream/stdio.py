"""Stdio MCP upstream pool adapter.

The adapter owns local child-process MCP sessions and exposes the domain
``UpstreamPool`` / ``ToolCaller`` ports. Each upstream session has its own
``call_lock`` so calls to one stdio child are queued while calls to other
children may proceed concurrently.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Self, cast

from vmcp.domain.models import JsonValue, ServerId, ToolCall, ToolResult

DEFAULT_SPAWN_TIMEOUT_SECONDS = 30.0
DEFAULT_CALL_TIMEOUT_SECONDS = 60.0
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0
MCP_PROTOCOL_VERSION = "2024-11-05"


class UpstreamError(RuntimeError):
    """Base class for stdio upstream adapter errors."""


class UpstreamUnavailableError(UpstreamError):
    """Raised when a caller is requested for an unavailable upstream."""


class UpstreamSpawnError(UpstreamError):
    """Raised when a stdio child cannot be started or initialized."""


class UpstreamProtocolError(UpstreamError):
    """Raised when an upstream speaks invalid MCP JSON-RPC over stdio."""


class UpstreamCallError(UpstreamError):
    """Raised when a tool call cannot be completed."""


@dataclass(frozen=True, slots=True)
class SpawnFailure:
    """A failed upstream spawn collected without aborting pool startup."""

    server_id: ServerId
    error: BaseException


@dataclass(frozen=True, slots=True)
class StdioUpstreamConfig:
    """Configuration for one local stdio MCP child process."""

    server_id: ServerId | str
    command: str
    args: Sequence[str] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str | Path | None = None
    spawn_timeout: float | None = None
    call_timeout: float | None = None

    def __post_init__(self) -> None:
        server_id = (
            self.server_id
            if isinstance(self.server_id, ServerId)
            else ServerId(value=self.server_id)
        )
        object.__setattr__(
            self,
            "server_id",
            server_id,
        )
        command = self.command.strip()
        if not command:
            raise ValueError("stdio upstream command must not be blank")

        object.__setattr__(self, "command", command)
        object.__setattr__(self, "args", tuple(str(arg) for arg in self.args))
        object.__setattr__(self, "env", dict(self.env))
        if self.cwd is not None:
            object.__setattr__(self, "cwd", Path(self.cwd))

        if self.spawn_timeout is not None and self.spawn_timeout <= 0:
            raise ValueError("spawn_timeout must be positive")
        if self.call_timeout is not None and self.call_timeout <= 0:
            raise ValueError("call_timeout must be positive")


class StdioProcess(Protocol):
    """Subset of ``asyncio.subprocess.Process`` used by the adapter."""

    returncode: int | None

    def terminate(self) -> None:
        """Ask the child to exit gracefully."""

    def kill(self) -> None:
        """Force-kill the child."""

    async def wait(self) -> int:
        """Wait for process exit and return the exit code."""


class StdioClient(Protocol):
    """Minimal MCP client shape owned by one upstream session."""

    process: StdioProcess | None

    async def call_tool(self, tool_name: str, arguments: Mapping[str, JsonValue]) -> Any:
        """Call an MCP tool and return the raw MCP result."""

    async def close(self) -> None:
        """Close client-side resources."""


ClientFactory = Callable[[StdioUpstreamConfig], Awaitable[StdioClient]]


@dataclass(slots=True)
class StdioUpstreamSession:
    """A running upstream plus its per-server call queue."""

    config: StdioUpstreamConfig
    client: StdioClient
    call_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    connected: bool = True

    @property
    def process(self) -> StdioProcess | None:
        """Return the child process owned by the MCP client, when available."""
        return self.client.process

    async def close(self, *, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS) -> None:
        """Close the session and reap its child process.

        Shutdown is best-effort: close the protocol stream, terminate the child,
        wait for it, and escalate to kill if it ignores the graceful request.
        """
        if not self.connected:
            return

        self.connected = False
        try:
            await self.client.close()
        finally:
            await _terminate_process(self.process, timeout=timeout)


class StdioToolCaller:
    """Domain ``ToolCaller`` implementation for one stdio upstream."""

    def __init__(self, session: StdioUpstreamSession, *, call_timeout: float) -> None:
        self._session = session
        self._call_timeout = call_timeout

    async def call_tool(self, call: ToolCall) -> ToolResult:
        """Queue and execute a tool call against this caller's upstream."""
        if call.server_id != self._session.config.server_id:
            raise UpstreamUnavailableError(
                "tool call server "
                f"{call.server_id.value!r} does not match caller server "
                f"{self._session.config.server_id.value!r}"
            )
        if not self._session.connected:
            raise UpstreamUnavailableError(f"upstream {call.server_id.value!r} is disconnected")

        async with self._session.call_lock:
            try:
                raw_result = await asyncio.wait_for(
                    self._session.client.call_tool(call.tool_name, call.arguments),
                    timeout=self._call_timeout,
                )
            except TimeoutError as exc:
                await self._session.close()
                raise UpstreamCallError(
                    f"upstream {call.server_id.value!r} tool {call.tool_name!r} timed out"
                ) from exc
            except UpstreamError:
                raise
            except Exception as exc:
                raise UpstreamCallError(
                    f"upstream {call.server_id.value!r} tool {call.tool_name!r} failed: {exc}"
                ) from exc

        return _to_tool_result(call, raw_result)


class StdioUpstreamPool:
    """Driven adapter that owns stdio MCP child sessions."""

    def __init__(
        self,
        configs: Sequence[StdioUpstreamConfig],
        *,
        client_factory: ClientFactory | None = None,
        default_spawn_timeout: float = DEFAULT_SPAWN_TIMEOUT_SECONDS,
        default_call_timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    ) -> None:
        if default_spawn_timeout <= 0:
            raise ValueError("default_spawn_timeout must be positive")
        if default_call_timeout <= 0:
            raise ValueError("default_call_timeout must be positive")
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be positive")

        self._configs = tuple(configs)
        self._client_factory = client_factory or StdioMcpClient.create
        self._default_spawn_timeout = default_spawn_timeout
        self._default_call_timeout = default_call_timeout
        self._shutdown_timeout = shutdown_timeout
        self._sessions: dict[str, StdioUpstreamSession] = {}
        self._spawn_failures: dict[str, SpawnFailure] = {}
        self._started = False

        seen: set[str] = set()
        for config in self._configs:
            server_key = config.server_id.value
            if server_key in seen:
                raise ValueError(f"duplicate upstream server id: {server_key!r}")
            seen.add(server_key)

    @property
    def spawn_failures(self) -> tuple[SpawnFailure, ...]:
        """Failures collected during the latest ``start`` call."""
        return tuple(self._spawn_failures.values())

    @property
    def active_server_ids(self) -> tuple[ServerId, ...]:
        """Currently available upstream server IDs."""
        return tuple(session.config.server_id for session in self._sessions.values())

    async def start(self) -> None:
        """Start all configured upstreams, keeping partial successes."""
        if self._started:
            return

        results = await asyncio.gather(
            *(self._start_one(config) for config in self._configs),
            return_exceptions=True,
        )

        for config, result in zip(self._configs, results, strict=True):
            server_key = config.server_id.value
            if isinstance(result, StdioUpstreamSession):
                self._sessions[server_key] = result
            else:
                error = (
                    result if isinstance(result, BaseException) else UpstreamSpawnError(str(result))
                )
                self._spawn_failures[server_key] = SpawnFailure(
                    server_id=config.server_id,
                    error=error,
                )

        self._started = True

    async def stop(self) -> None:
        """Stop all active upstream sessions and reap their children."""
        sessions = tuple(self._sessions.values())
        await asyncio.gather(
            *(session.close(timeout=self._shutdown_timeout) for session in sessions),
            return_exceptions=True,
        )
        self._sessions.clear()
        self._started = False

    def caller_for(self, server_id: ServerId) -> StdioToolCaller:
        """Return a queued ``ToolCaller`` for a running upstream."""
        server_key = server_id.value
        session = self._sessions.get(server_key)
        if session is None:
            failure = self._spawn_failures.get(server_key)
            if failure is not None:
                raise UpstreamUnavailableError(
                    f"upstream {server_key!r} failed to start: {failure.error}"
                ) from failure.error
            raise UpstreamUnavailableError(f"upstream {server_key!r} is not available")

        return StdioToolCaller(
            session,
            call_timeout=session.config.call_timeout or self._default_call_timeout,
        )

    async def _start_one(self, config: StdioUpstreamConfig) -> StdioUpstreamSession:
        timeout = config.spawn_timeout or self._default_spawn_timeout
        try:
            client = await asyncio.wait_for(self._client_factory(config), timeout=timeout)
        except TimeoutError as exc:
            msg = f"upstream {config.server_id.value!r} spawn timed out"
            raise UpstreamSpawnError(msg) from exc
        except UpstreamError:
            raise
        except Exception as exc:
            raise UpstreamSpawnError(
                f"upstream {config.server_id.value!r} failed to start: {exc}"
            ) from exc

        return StdioUpstreamSession(config=config, client=client)


class StdioMcpClient:
    """Small MCP JSON-RPC client over a child process's stdio pipes."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        if process.stdin is None or process.stdout is None:
            raise UpstreamSpawnError("stdio upstream process must expose stdin and stdout pipes")

        self.process: asyncio.subprocess.Process | None = process
        self._stdin = process.stdin
        self._stdout = process.stdout
        self._next_request_id = 1
        self._request_lock = asyncio.Lock()
        self._closed = False

    @classmethod
    async def create(cls, config: StdioUpstreamConfig) -> Self:
        """Spawn a child process and complete the MCP initialize handshake."""
        env = os.environ.copy()
        env.update(config.env)
        try:
            process = await asyncio.create_subprocess_exec(
                config.command,
                *config.args,
                cwd=str(config.cwd) if config.cwd is not None else None,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            raise UpstreamSpawnError(
                f"could not spawn upstream {config.server_id.value!r}: {exc}"
            ) from exc

        client = cls(process)
        try:
            await client.initialize()
        except BaseException:
            await client.close()
            await _terminate_process(process, timeout=DEFAULT_SHUTDOWN_TIMEOUT_SECONDS)
            raise
        return client

    async def initialize(self) -> None:
        """Perform the MCP initialize request and initialized notification."""
        await self._request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "vmcp-lite", "version": "0.1.0"},
            },
        )
        await self._notify("notifications/initialized", {})

    async def call_tool(self, tool_name: str, arguments: Mapping[str, JsonValue]) -> JsonValue:
        """Call ``tools/call`` and return the raw MCP result payload."""
        result = await self._request(
            "tools/call",
            {"name": tool_name, "arguments": dict(arguments)},
        )
        return cast(JsonValue, result)

    async def close(self) -> None:
        """Close the stdin pipe; process reaping is handled by the session."""
        if self._closed:
            return
        self._closed = True
        self._stdin.close()
        with suppress(BrokenPipeError, ConnectionResetError):
            await self._stdin.wait_closed()

    async def _request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        if self._closed:
            raise UpstreamProtocolError("stdio MCP client is closed")

        async with self._request_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            await self._write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": dict(params or {}),
                }
            )

            while True:
                message = await self._read_message()
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise UpstreamCallError(f"{method} failed: {message['error']}")
                return message.get("result")

    async def _notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        if self._closed:
            raise UpstreamProtocolError("stdio MCP client is closed")
        await self._write_message(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": dict(params or {}),
            }
        )

    async def _write_message(self, message: Mapping[str, Any]) -> None:
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._stdin.write(header + body)
        try:
            await self._stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise UpstreamProtocolError("stdio upstream pipe closed while writing") from exc

    async def _read_message(self) -> dict[str, Any]:
        content_length: int | None = None
        while True:
            line = await self._stdout.readline()
            if not line:
                raise UpstreamProtocolError("stdio upstream closed stdout")
            if line in (b"\r\n", b"\n"):
                break
            key, separator, value = line.decode("ascii", errors="replace").partition(":")
            if not separator:
                raise UpstreamProtocolError(f"invalid stdio header line: {line!r}")
            if key.lower() == "content-length":
                try:
                    content_length = int(value.strip())
                except ValueError as exc:
                    raise UpstreamProtocolError("invalid Content-Length header") from exc

        if content_length is None:
            raise UpstreamProtocolError("missing Content-Length header")

        body = await self._stdout.readexactly(content_length)
        try:
            message = json.loads(body)
        except json.JSONDecodeError as exc:
            raise UpstreamProtocolError("invalid JSON-RPC message body") from exc
        if not isinstance(message, dict):
            raise UpstreamProtocolError("JSON-RPC message must be an object")
        return cast(dict[str, Any], message)


async def _terminate_process(
    process: StdioProcess | None,
    *,
    timeout: float,
) -> None:
    if process is None or process.returncode is not None:
        return

    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
        return
    except TimeoutError:
        process.kill()
        await process.wait()


def _to_tool_result(call: ToolCall, raw_result: Any) -> ToolResult:
    if isinstance(raw_result, ToolResult):
        return raw_result

    if isinstance(raw_result, Mapping):
        is_error = bool(raw_result.get("isError", raw_result.get("is_error", False)))
        return ToolResult(
            server_id=call.server_id,
            tool_name=call.tool_name,
            content=cast(JsonValue, raw_result.get("content", raw_result)),
            is_error=is_error,
            error_message=_extract_error_message(raw_result) if is_error else None,
        )

    return ToolResult(
        server_id=call.server_id,
        tool_name=call.tool_name,
        content=cast(JsonValue, raw_result),
    )


def _extract_error_message(raw_result: Mapping[str, Any]) -> str | None:
    explicit = raw_result.get("error_message")
    if isinstance(explicit, str):
        return explicit

    content = raw_result.get("content")
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        for item in content:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                return cast(str, item["text"])
    return None
