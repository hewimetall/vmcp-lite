"""GraphQL boundary models owned by the Python domain layer."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vmcp.domain.models.types import JsonValue


class GraphQLRequest(BaseModel):
    """Lite query_graphql request passed to the schema engine boundary."""

    model_config = ConfigDict(frozen=True)

    query: str
    variables: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator("query")
    @classmethod
    def _query_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("GraphQL query must not be blank")
        return normalized


class GraphQLError(BaseModel):
    """Serializable GraphQL-style error shape for lite responses."""

    model_config = ConfigDict(frozen=True)

    message: str
    path: tuple[str | int, ...] = ()
    extensions: Mapping[str, JsonValue] = Field(default_factory=dict)


class GraphQLResponse(BaseModel):
    """Lite GraphQL response shape returned by the query use case."""

    model_config = ConfigDict(frozen=True)

    data: JsonValue = None
    errors: tuple[GraphQLError, ...] = ()
    extensions: Mapping[str, JsonValue] = Field(default_factory=dict)

