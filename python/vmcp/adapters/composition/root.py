"""Composition root placeholder for ADR-0014."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompositionRoot:
    """Container for vmcp-lite dependencies.

    TODO: wire domain use cases to FastMCP, registry, upstream, CallBridge, and
    GraphQL adapters once those contracts are defined.
    """

    configured: bool = False


def build_composition_root() -> CompositionRoot:
    """Build the scaffold composition root."""
    return CompositionRoot()
