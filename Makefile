.PHONY: build test lint

build:
	uv run --extra dev maturin develop

test:
	uv run --extra dev pytest

lint:
	uv run --extra dev ruff check python tests
