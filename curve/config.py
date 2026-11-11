from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeAlias

__all__ = [
    "CONFIG_DIR_NAME",
    "CONFIG_FILE_NAME",
    "config_dir",
    "config_path",
    "read_config",
    "write_config",
]

CONFIG_DIR_NAME = ".curve"
CONFIG_FILE_NAME = "config.toml"

PathLike: TypeAlias = str | os.PathLike[str]
Config: TypeAlias = dict[str, Any]

_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def config_dir() -> Path:
    override = os.environ.get("CURVE_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / CONFIG_DIR_NAME


def config_path() -> Path:
    return config_dir() / CONFIG_FILE_NAME


def read_config(path: PathLike | None = None) -> Config:
    config_file = _resolve_path(path)
    try:
        with config_file.open("rb") as file:
            return tomllib.load(file)
    except FileNotFoundError:
        return {}


def write_config(config: Mapping[str, Any], path: PathLike | None = None) -> Path:

    if not isinstance(config, Mapping):
        raise TypeError("config must be a mapping")

    config_file = _resolve_path(path)
    config_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    text = _dumps(config)
    _write_atomic(config_file, text, mode=0o600)
    return config_file


def _resolve_path(path: PathLike | None) -> Path:
    return config_path() if path is None else Path(path).expanduser()


def _write_atomic(path: Path, text: str, *, mode: int | None = None) -> None:
    fd, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _dumps(config: Mapping[str, Any]) -> str:
    lines: list[str] = []
    _dump_table(config, [], lines)
    return "\n".join(lines).strip() + "\n" if lines else ""


def _dump_table(
    table: Mapping[str, Any],
    prefix: list[str],
    lines: list[str],
) -> None:
    values: list[tuple[str, Any]] = []
    tables: list[tuple[str, Any, bool]] = []

    for key, value in table.items():
        if not isinstance(key, str):
            raise TypeError("config keys must be strings")
        if isinstance(value, Mapping):
            tables.append((key, value, False))
        elif (
            isinstance(value, (list, tuple))
            and value
            and all(isinstance(item, Mapping) for item in value)
        ):
            tables.append((key, value, True))
        else:
            values.append((key, value))

    for key, value in values:
        lines.append(f"{_format_key(key)} = {_format_value(value)}")

    for key, value, is_array in tables:
        path = [*prefix, key]
        header = ".".join(_format_key(part) for part in path)
        if is_array:
            for item in value:
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(f"[[{header}]]")
                _dump_table(item, path, lines)
        else:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"[{header}]")
            _dump_table(value, path, lines)


def _format_key(key: str) -> str:
    if _BARE_KEY_RE.fullmatch(key):
        return key
    return json.dumps(key, ensure_ascii=False)


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    raise TypeError(
        f"cannot write {type(value).__name__} to TOML; "
        "use strings, numbers, booleans, dates/times, arrays or tables"
    )
