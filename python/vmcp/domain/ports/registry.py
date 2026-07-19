"""Ports for loading upstream registry snapshots."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from vmcp.domain.models import ToolRegistry


@runtime_checkable
class RegistryLoader(Protocol):
    """Loads the tool registry used by the virtual GraphQL schema."""

    async def load_registry(self) -> ToolRegistry:
        """Return the current upstream tool registry snapshot."""
        ...

