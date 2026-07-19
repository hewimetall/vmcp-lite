"""Domain use cases for vmcp-lite."""

from vmcp.domain.usecases.boot import compose_usecases
from vmcp.domain.usecases.query_graphql import (
    ExecuteQueryGraphQL,
    build_query_graphql_usecase,
)

__all__ = [
    "ExecuteQueryGraphQL",
    "build_query_graphql_usecase",
    "compose_usecases",
]
