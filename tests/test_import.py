"""Smoke tests for the vmcp-lite scaffold."""

from __future__ import annotations


def test_import_vmcp() -> None:
    """The top-level package imports before behavior is implemented."""
    import vmcp

    assert vmcp.__all__ == []
