from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


def project_root_from(path: Path) -> Path:
    current = path.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "docker-compose.yml").exists():
            return candidate
    raise RuntimeError(f"Could not resolve project root from {path}")


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    with path.open("rb") as fp:
        config = tomllib.load(fp)
    config["_config_path"] = str(path.resolve())
    config["_project_root"] = str(project_root_from(path))
    return config


def get_nested(config: dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    value: Any = config
    for part in dotted_key.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def resolve_path(config: dict[str, Any], value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path(str(config["_project_root"])) / path


def map_runtime_data_path(value: str | Path) -> Path:
    normalized = str(value).replace("\\", "/")
    host_root = os.getenv("EVM_HOST_DATA_ROOT", "").replace("\\", "/").rstrip("/")
    mount_root = os.getenv("EVM_DATA_MOUNT_ROOT", "").replace("\\", "/").rstrip("/")
    if host_root and mount_root and normalized.lower().startswith(host_root.lower()):
        normalized = f"{mount_root}{normalized[len(host_root):]}"
    return Path(normalized)
