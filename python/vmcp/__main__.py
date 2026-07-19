"""Command-line entry point for vmcp-lite."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Create the placeholder CLI parser."""
    parser = argparse.ArgumentParser(
        prog="vmcp-lite",
        description="stdio-only virtual MCP gateway (scaffold)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="vmcp-lite 0.1.0",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the scaffold CLI and exit successfully."""
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
