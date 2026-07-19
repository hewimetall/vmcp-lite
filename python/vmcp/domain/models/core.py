"""Core domain models for vmcp-lite."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vmcp.domain.models.types import JsonValue


class ServerId(BaseModel):
    """Identifier for an upstream MCP server."""

    model_config = ConfigDict(frozen=True)

    value: str

    @field_validator("value")
    @classmethod
    def _must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("server id must not be blank")
        return normalized


class ToolCall(BaseModel):
    """Request to call a tool exposed by an upstream MCP server."""

    model_config = ConfigDict(frozen=True)

    server_id: ServerId
    tool_name: str
    arguments: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator("tool_name")
    @classmethod
    def _tool_name_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("tool name must not be blank")
        return normalized


class ToolResult(BaseModel):
    """Result returned by an upstream MCP tool call."""

    model_config = ConfigDict(frozen=True)

    server_id: ServerId
    tool_name: str
    content: JsonValue = None
    is_error: bool = False
    error_message: str | None = None


class ToolDefinition(BaseModel):
    """Tool metadata loaded from upstream registry discovery."""

    model_config = ConfigDict(frozen=True)

    server_id: ServerId
    name: str
    description: str = ""
    input_schema: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("tool definition name must not be blank")
        return normalized


class ToolRegistry(BaseModel):
    """Registry snapshot used to build or execute the virtual GraphQL schema."""

    model_config = ConfigDict(frozen=True)

    tools: tuple[ToolDefinition, ...] = ()

