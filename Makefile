.PHONY: build test lint demo

build:
	uv run --extra dev maturin develop

test:
	uv run --extra dev pytest

lint:
	uv run --extra dev ruff check python tests

demo:
	uv run vmcp-lite --config "$(CURDIR)/examples/demo/vmcp.toml"
