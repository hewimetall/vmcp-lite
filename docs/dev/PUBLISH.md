# Publishing vmcp-lite-mcp

## One-time PyPI setup

Create the PyPI project `vmcp-lite-mcp` and configure Trusted Publishing:

- Owner: the PyPI account or organization that owns the package.
- Publisher: GitHub.
- Repository owner: `hewimetall`.
- Repository name: `vmcp-lite`.
- Workflow name: `release.yml`.
- Environment name: `pypi`.

The release workflow publishes from the GitHub environment `pypi` and uses the
project URL `https://pypi.org/p/vmcp-lite-mcp`.

If Trusted Publishing is not configured yet, add a repository or environment
secret named `PYPI_API_TOKEN`; the workflow uses Trusted Publishing when this
secret is absent and uses the token fallback when the secret is present.

## Release steps

1. Bump the version in `pyproject.toml`.
2. Keep the Rust package version in `Cargo.toml` in sync with the Python
   version.
3. Refresh locks and run validation:

   ```bash
   uv lock
   uv sync --extra dev
   uv run --extra dev maturin develop
   uv run pytest
   uv run ruff check python tests
   cargo test
   ```

4. Commit the version and lockfile changes.
5. Create and push a `v*` tag, for example:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

6. GitHub Actions builds wheels, an sdist, creates a GitHub Release, and
   publishes to PyPI.

## Install the published package

```bash
pip install vmcp-lite-mcp
uvx vmcp-lite-mcp
```
