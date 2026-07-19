"""File-backed stdio registry loader for vmcp-lite."""

from __future__ import annotations

import json
import logging
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vmcp.domain.models import ServerId, ToolDefinition, ToolRegistry

try:  # pragma: no cover - exercised only when PyYAML is absent.
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)


class RegistryLoadError(ValueError):
    """Raised when the top-level registry file cannot be loaded at all."""


class RegistryConfigError(ValueError):
    """Raised when vmcp.toml registry settings are malformed."""


@dataclass(frozen=True, slots=True)
class RegistryValidationIssue:
    """Non-fatal validation issue collected while loading registry entries."""

    location: str
    message: str


@dataclass(frozen=True, slots=True)
class StdioUpstreamConfig:
    """Validated stdio upstream configuration from the registry file."""

    name: str
    description: str | None
    command: str
    args: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path | None
    sidecar_spec: Path | None


@dataclass(frozen=True, slots=True)
class RegistryLoaderSettings:
    """Registry-related settings read from vmcp.toml."""

    registry_path: Path
    spec_dir: Path

    @classmethod
    def from_config_file(cls, config_path: str | Path) -> RegistryLoaderSettings:
        """Load registry paths from a minimal vmcp.toml file."""
        path = Path(config_path)
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RegistryConfigError(f"cannot read config file {path}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise RegistryConfigError(f"cannot parse config file {path}: {exc}") from exc

        registry_section = raw.get("registry", {})
        if registry_section is None:
            registry_section = {}
        if not isinstance(registry_section, Mapping):
            raise RegistryConfigError("[registry] must be a table when present")

        registry_path = _string_setting(
            raw.get("registry_path", registry_section.get("path", "registry.json")),
            "registry_path",
        )
        spec_dir = _string_setting(
            raw.get("spec_dir", registry_section.get("spec_dir", "specs")),
            "spec_dir",
        )

        base_dir = path.parent
        return cls(
            registry_path=_resolve_path(base_dir, registry_path),
            spec_dir=_resolve_path(base_dir, spec_dir),
        )


class SidecarRegistryLoader:
    """Load stdio upstream tools from registry entries and sidecar specs."""

    def __init__(self, registry_path: str | Path, *, spec_dir: str | Path | None = None) -> None:
        self._registry_path = Path(registry_path)
        self._spec_dir = self._resolve_spec_dir(spec_dir)
        self._validation_issues: list[RegistryValidationIssue] = []
        self._upstreams: list[StdioUpstreamConfig] = []

    @property
    def registry_path(self) -> Path:
        """Registry document path."""
        return self._registry_path

    @property
    def spec_dir(self) -> Path:
        """Directory containing sidecar specs."""
        return self._spec_dir

    @property
    def validation_issues(self) -> tuple[RegistryValidationIssue, ...]:
        """Non-fatal issues from the most recent load."""
        return tuple(self._validation_issues)

    @property
    def upstreams(self) -> tuple[StdioUpstreamConfig, ...]:
        """Validated stdio upstreams from the most recent load."""
        return tuple(self._upstreams)

    @classmethod
    def from_config_file(cls, config_path: str | Path) -> SidecarRegistryLoader:
        """Build a loader from vmcp.toml registry settings."""
        settings = RegistryLoaderSettings.from_config_file(config_path)
        return cls(settings.registry_path, spec_dir=settings.spec_dir)

    async def load_registry(self) -> ToolRegistry:
        """Return a registry snapshot built from valid stdio sidecar entries."""
        self._validation_issues = []
        self._upstreams = []

        if not self._registry_path.exists():
            LOGGER.warning(
                "registry file %s not found; starting with no upstreams",
                self._registry_path,
            )
            return ToolRegistry()

        document = _load_mapping_file(self._registry_path)
        upstreams = self._load_upstreams(document)
        tools = self._load_tools(upstreams)
        return ToolRegistry(tools=tuple(tools))

    def _resolve_spec_dir(self, spec_dir: str | Path | None) -> Path:
        if spec_dir is None:
            return self._registry_path.parent / "specs"
        path = Path(spec_dir)
        if path.is_absolute():
            return path
        return self._registry_path.parent / path

    def _load_upstreams(self, document: Mapping[str, Any]) -> list[StdioUpstreamConfig]:
        upstreams: list[StdioUpstreamConfig] = []
        seen_names: set[str] = set()

        for location, raw_entry in self._iter_upstream_entries(document):
            upstream = self._parse_upstream(location, raw_entry)
            if upstream is None:
                continue
            if upstream.name in seen_names:
                self._issue(location, f"duplicate upstream name {upstream.name!r}; entry skipped")
                continue
            seen_names.add(upstream.name)
            upstreams.append(upstream)

        self._upstreams = upstreams
        return upstreams

    def _iter_upstream_entries(
        self,
        document: Mapping[str, Any],
    ) -> list[tuple[str, Any]]:
        entries: list[tuple[str, Any]] = []
        for key in ("upstreams", "servers"):
            if key not in document:
                continue
            raw_entries = document[key]
            if not isinstance(raw_entries, list):
                self._issue(key, "must be a list; entries skipped")
                continue
            entries.extend((f"{key}[{index}]", entry) for index, entry in enumerate(raw_entries))
        return entries

    def _parse_upstream(self, location: str, raw_entry: Any) -> StdioUpstreamConfig | None:
        if not isinstance(raw_entry, Mapping):
            self._issue(location, "must be an object; entry skipped")
            return None

        enabled = raw_entry.get("enabled", True)
        if not isinstance(enabled, bool):
            self._issue(f"{location}.enabled", "must be a boolean; entry skipped")
            return None
        if not enabled:
            return None

        name = self._required_string(raw_entry, "name", location)
        if name is None:
            return None

        transport = self._optional_string(raw_entry, "transport", location, default="stdio")
        if transport is None:
            return None
        if transport.lower() != "stdio" or raw_entry.get("url") is not None:
            self._issue(location, "HTTP upstreams are not supported by vmcp-lite; entry skipped")
            return None

        command = self._required_string(raw_entry, "command", location)
        if command is None:
            return None

        description = self._optional_string(raw_entry, "description", location, default=None)
        if raw_entry.get("description") is not None and description is None:
            return None

        args = self._string_tuple(raw_entry.get("args", []), f"{location}.args")
        if args is None:
            return None

        env = self._string_mapping(raw_entry.get("env", {}), f"{location}.env")
        if env is None:
            return None

        cwd = self._optional_path(raw_entry, "cwd", location, base_dir=self._registry_path.parent)
        if raw_entry.get("cwd") is not None and cwd is None:
            return None

        sidecar_spec = self._optional_path(
            raw_entry,
            "sidecar_spec",
            location,
            base_dir=self._spec_dir,
        )
        if raw_entry.get("sidecar_spec") is not None and sidecar_spec is None:
            return None

        return StdioUpstreamConfig(
            name=name,
            description=description,
            command=command,
            args=args,
            env=env,
            cwd=cwd,
            sidecar_spec=sidecar_spec,
        )

    def _load_tools(self, upstreams: list[StdioUpstreamConfig]) -> list[ToolDefinition]:
        tools: list[ToolDefinition] = []
        seen_tools: set[tuple[str, str]] = set()

        for upstream in upstreams:
            for tool in self._load_sidecar_tools(upstream):
                identity = (tool.server_id.value, tool.name)
                if identity in seen_tools:
                    self._issue(
                        f"{upstream.name}.{tool.name}",
                        "duplicate tool definition; entry skipped",
                    )
                    continue
                seen_tools.add(identity)
                tools.append(tool)

        return tools

    def _load_sidecar_tools(self, upstream: StdioUpstreamConfig) -> list[ToolDefinition]:
        if upstream.sidecar_spec is None:
            return []
        if not upstream.sidecar_spec.exists():
            self._issue(
                str(upstream.sidecar_spec),
                f"sidecar for upstream {upstream.name!r} not found; "
                "upstream accepted with no tools",
            )
            return []

        try:
            document = _load_mapping_file(upstream.sidecar_spec)
        except RegistryLoadError as exc:
            self._issue(
                str(upstream.sidecar_spec),
                f"sidecar for upstream {upstream.name!r} could not be loaded: {exc}",
            )
            return []

        server_name = document.get("server")
        if server_name is not None:
            if not isinstance(server_name, str) or not server_name.strip():
                self._issue(f"{upstream.sidecar_spec}.server", "must be a non-empty string")
                return []
            if server_name.strip() != upstream.name:
                self._issue(
                    f"{upstream.sidecar_spec}.server",
                    f"must match upstream name {upstream.name!r}; sidecar skipped",
                )
                return []

        raw_tools = document.get("tools", [])
        if not isinstance(raw_tools, list):
            self._issue(f"{upstream.sidecar_spec}.tools", "must be a list; sidecar skipped")
            return []

        tools: list[ToolDefinition] = []
        for index, raw_tool in enumerate(raw_tools):
            tool = self._parse_sidecar_tool(
                f"{upstream.sidecar_spec}.tools[{index}]",
                upstream.name,
                raw_tool,
            )
            if tool is not None:
                tools.append(tool)
        return tools

    def _parse_sidecar_tool(
        self,
        location: str,
        upstream_name: str,
        raw_tool: Any,
    ) -> ToolDefinition | None:
        if not isinstance(raw_tool, Mapping):
            self._issue(location, "must be an object; tool skipped")
            return None

        name = self._required_string(raw_tool, "name", location)
        if name is None:
            return None

        read_only = raw_tool.get("read_only", False)
        if not isinstance(read_only, bool):
            self._issue(f"{location}.read_only", "must be a boolean; tool skipped")
            return None

        description = self._optional_string(raw_tool, "description", location, default="")
        if raw_tool.get("description") is not None and description is None:
            return None

        input_schema = raw_tool.get("input_schema", {})
        if not isinstance(input_schema, Mapping):
            self._issue(f"{location}.input_schema", "must be an object; tool skipped")
            return None

        return ToolDefinition(
            server_id=ServerId(value=upstream_name),
            name=name,
            description=description or "",
            input_schema=dict(input_schema),
            read_only=read_only,
        )

    def _required_string(
        self,
        raw_mapping: Mapping[str, Any],
        key: str,
        location: str,
    ) -> str | None:
        if key not in raw_mapping:
            self._issue(f"{location}.{key}", "is required; entry skipped")
            return None
        value = raw_mapping[key]
        if not isinstance(value, str):
            self._issue(f"{location}.{key}", "must be a string; entry skipped")
            return None
        normalized = value.strip()
        if not normalized:
            self._issue(f"{location}.{key}", "must not be blank; entry skipped")
            return None
        return normalized

    def _optional_string(
        self,
        raw_mapping: Mapping[str, Any],
        key: str,
        location: str,
        *,
        default: str | None,
    ) -> str | None:
        if key not in raw_mapping:
            return default
        value = raw_mapping[key]
        if value is None:
            return default
        if not isinstance(value, str):
            self._issue(f"{location}.{key}", "must be a string")
            return None
        return value.strip()

    def _optional_path(
        self,
        raw_mapping: Mapping[str, Any],
        key: str,
        location: str,
        *,
        base_dir: Path,
    ) -> Path | None:
        value = self._optional_string(raw_mapping, key, location, default=None)
        if value is None:
            return None
        if not value:
            self._issue(f"{location}.{key}", "must not be blank")
            return None
        return _resolve_path(base_dir, value)

    def _string_tuple(self, raw_value: Any, location: str) -> tuple[str, ...] | None:
        if raw_value is None:
            return ()
        if not isinstance(raw_value, list):
            self._issue(location, "must be a list of strings; entry skipped")
            return None
        values: list[str] = []
        for index, value in enumerate(raw_value):
            if not isinstance(value, str):
                self._issue(f"{location}[{index}]", "must be a string; entry skipped")
                return None
            values.append(value)
        return tuple(values)

    def _string_mapping(self, raw_value: Any, location: str) -> dict[str, str] | None:
        if raw_value is None:
            return {}
        if not isinstance(raw_value, Mapping):
            self._issue(location, "must be an object of string values; entry skipped")
            return None
        env: dict[str, str] = {}
        for key, value in raw_value.items():
            if not isinstance(key, str) or not isinstance(value, str):
                self._issue(f"{location}.{key}", "must be a string value; entry skipped")
                return None
            env[key] = value
        return env

    def _issue(self, location: str, message: str) -> None:
        issue = RegistryValidationIssue(location=location, message=message)
        self._validation_issues.append(issue)
        LOGGER.warning("registry validation issue at %s: %s", location, message)


def _load_mapping_file(path: Path) -> Mapping[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryLoadError(f"cannot read {path}: {exc}") from exc

    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            raw = json.loads(text or "{}")
        elif suffix in {".yaml", ".yml"}:
            if yaml is None:
                raise RegistryLoadError("PyYAML is required to load YAML registry files")
            raw = yaml.safe_load(text) or {}
        else:
            raise RegistryLoadError(f"unsupported file extension {suffix!r}")
    except json.JSONDecodeError as exc:
        raise RegistryLoadError(f"cannot parse JSON {path}: {exc}") from exc
    except Exception as exc:
        if isinstance(exc, RegistryLoadError):
            raise
        raise RegistryLoadError(f"cannot parse YAML {path}: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise RegistryLoadError(f"{path} must contain an object at the top level")
    return raw


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def _string_setting(value: Any, key: str) -> str:
    if not isinstance(value, str):
        raise RegistryConfigError(f"{key} must be a string")
    normalized = value.strip()
    if not normalized:
        raise RegistryConfigError(f"{key} must not be blank")
    return normalized


__all__ = [
    "RegistryConfigError",
    "RegistryLoadError",
    "RegistryLoaderSettings",
    "RegistryValidationIssue",
    "SidecarRegistryLoader",
    "StdioUpstreamConfig",
]
