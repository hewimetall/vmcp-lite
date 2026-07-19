"""Command-line entry point for vmcp-lite."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from vmcp.adapters.composition.root import boot_composition_root
from vmcp.adapters.driving.fastmcp_server import create_server


def build_parser() -> argparse.ArgumentParser:
    """Create the vmcp-lite CLI parser."""
    parser = argparse.ArgumentParser(
        prog="vmcp-lite",
        description="stdio-only virtual MCP gateway",
    )
    parser.add_argument(
        "config_path",
        nargs="?",
        type=Path,
        help="path to vmcp.toml",
    )
    parser.add_argument(
        "--config",
        dest="config_path_option",
        type=Path,
        help="path to vmcp.toml (alternative to positional config_path)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="FastMCP stdio log level",
    )
    parser.add_argument(
        "--no-banner",
        action="store_true",
        help="hide the FastMCP startup banner",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="vmcp-lite 0.1.0",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the FastMCP stdio server."""
    parser = build_parser()
    args = parser.parse_args(argv)

    config_path = args.config_path_option or args.config_path
    if config_path is None:
        parser.error("a config path is required")
    if args.config_path_option is not None and args.config_path is not None:
        parser.error("provide config_path either positionally or with --config, not both")

    try:
        asyncio.run(
            _run_stdio_server(config_path, log_level=args.log_level, banner=not args.no_banner)
        )
    except KeyboardInterrupt:
        return 130
    return 0


async def _run_stdio_server(
    config_path: Path,
    *,
    log_level: str | None,
    banner: bool,
) -> None:
    root = await boot_composition_root(config_path=config_path)
    try:
        server = create_server(root)
        await server.run_stdio_async(show_banner=banner, log_level=log_level)
    finally:
        await root.stop()


if __name__ == "__main__":
    raise SystemExit(main())
