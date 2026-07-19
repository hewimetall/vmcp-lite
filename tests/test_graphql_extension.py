"""Smoke tests for the optional vmcp._graphql PyO3 extension."""

from __future__ import annotations

import json

import pytest


def test_schema_engine_extension_smoke() -> None:
    """The maturin-built extension exposes the SchemaEngine stub contract."""
    graphql = pytest.importorskip("vmcp._graphql", reason="vmcp._graphql extension is not built")

    engine = graphql.SchemaEngine.build('[{"server": "demo", "name": "echo"}]')
    assert engine.tool_count() == 1

    response = json.loads(
        engine.execute('{ __typename demo_echo { isError text json } }', "{}")
    )

    assert response["errors"] == []
    assert response["data"]["__typename"] == "Query"
    assert response["data"]["demo_echo"]["isError"] is True
    assert "CallBridge is not wired yet" in response["data"]["demo_echo"]["text"]
