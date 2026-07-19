# Development setup

vmcp-lite targets Python 3.14 per ADR-0020 and uses uv for Python
installation, dependency resolution, virtualenv management, and command
execution. The Rust/PyO3 extension is built with maturin.

## Required tools

- uv 0.11 or newer
- Python 3.14, installed with uv
- Rust stable with Cargo
- maturin, provided by the `dev` extra

## Bootstrap

```bash
curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

uv python install 3.14
uv sync --extra dev
uv run --extra dev maturin develop
uv run python --version
uv run pytest
```

The repository includes `.python-version` with `3.14`, so `uv run` and
`uv sync` use a Python 3.14 interpreter once uv has installed it.

## Cursor Cloud persistence

No Cursor `environment.json` or setup script is currently present in this
repository or under `/tmp`. To make future Cursor Cloud agents start with this
toolchain, run a Cursor Onboard environment setup agent with:

```text
For /workspace vmcp-lite, persist the Python/Rust toolchain used by agents:
install uv on PATH, run `uv python install 3.14`, ensure Rust stable and Cargo
are available, and keep `$HOME/.local/bin` on PATH for non-interactive shells.
Verify with `uv --version`, `uv run python --version`, `cargo --version`, and
`uv sync --extra dev`.
```
