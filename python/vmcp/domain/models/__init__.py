"""Domain models for vmcp-lite."""

from vmcp.domain.models.core import (
    ServerId,
    ToolCall,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)
from vmcp.domain.models.graphql import GraphQLError, GraphQLRequest, GraphQLResponse
from vmcp.domain.models.types import JsonValue

__all__ = [
    "GraphQLError",
    "GraphQLRequest",
    "GraphQLResponse",
    "JsonValue",
    "ServerId",
    "ToolCall",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
]
