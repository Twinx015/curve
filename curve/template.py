from __future__ import annotations

import datetime as dt
import getpass
import importlib.resources
import json
import os
import re
import socket
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import encrypt as crypto
from .config import config_dir, read_config

__all__ = [
    "TEMPLATE_SUFFIX",
    "TEMPLATE_SUFFIXES",
    "SEPARATOR",
    "BUILTIN_FIELDS",
    "DEFAULT_FILENAME",
    "DEFAULT_EXTENSION",
    "TemplateError",
    "FieldSpec",
    "TemplateOptions",
    "Template",
    "parse_template",
    "load_template",
    "builtin_templates_dir",
    "templates_dir",
    "template_dirs",
    "list_templates",
    "resolve_template",
    "copy_builtin_templates",
    "resolve_fields",
    "render",
    "entry_path",
    "write_entry",
    "get_password",
]

TEMPLATE_SUFFIX = ".ext"
TEMPLATE_SUFFIXES = (".ext", ".cuv")
SEPARATOR = "--"
DEFAULT_FILENAME = "{date}-{template}"
DEFAULT_EXTENSION = "md"

#: Tokens Curve can fill automatically.
BUILTIN_FIELDS = (
    "day",
    "date",
    "time",
    "clock",
    "datetime",
    "weekday",
    "week",
    "month",
    "year",
    "timestamp",
    "template",
    "user",
    "host",
)

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}

_OPTION_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*=\s*(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})([A-Za-z_][\w-]*)\s*$")
_PLACEHOLDER_RE = re.compile(r"\{\{?\s*([A-Za-z_][\w-]*)\s*\}?\}")

_OPTION_ALIASES = {
    "dir": "directory",
    "file": "filename",
    "ext": "extension",
    "encrypted": "encrypt",
    "auto": "autofill",
    "auto_fill": "autofill",
}


class TemplateError(Exception):
    """Raised when a template is missing or malformed."""


@dataclass(frozen=True)
class FieldSpec:
    """A custom field that should be prompted for."""

    name: str
    default: str | None = None


@dataclass
class TemplateOptions:
    """Known options from the header of a template."""

    name: str
    description: str = ""
    directory: str = ""
    filename: str = DEFAULT_FILENAME
    extension: str = DEFAULT_EXTENSION
    encrypt: bool = False
    autofill: bool = True
    fields: list[FieldSpec] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Template:
    """A parsed ``.ext`` template."""

    name: str
    options: TemplateOptions
    header: list[str]
    body: str
    source: Path | None = None

    def declared_fields(self) -> list[FieldSpec]:
        """Return custom fields, in the order they should be asked for."""
        specs: list[FieldSpec] = []
        seen: set[str] = set()
        for line in self.header:
            match = _HEADING_RE.match(line.strip())
            if not match:
                continue
            name = match.group(2)
            if name not in BUILTIN_FIELDS and name not in seen:
                specs.append(FieldSpec(name))
                seen.add(name)
        for spec in self.options.fields:
            if spec.name in BUILTIN_FIELDS or spec.name in seen:
                continue
            specs.append(spec)
            seen.add(spec.name)
        return specs

    def used_tokens(self) -> set[str]:
        """Every token referenced by the header, body or output name."""
        tokens: set[str] = set()
        for line in self.header:
            match = _HEADING_RE.match(line.strip())
            if match:
                tokens.add(match.group(2))
            else:
                tokens.update(_PLACEHOLDER_RE.findall(line))
        tokens.update(_PLACEHOLDER_RE.findall(self.body))
        tokens.update(_PLACEHOLDER_RE.findall(self.options.filename))
        tokens.update(_PLACEHOLDER_RE.findall(self.options.directory))
        return tokens

    def heading_tokens(self) -> list[tuple[int, str]]:
        """Return ``(level, token)`` pairs for generated headings."""
        headings: list[tuple[int, str]] = []
        for line in self.header:
            match = _HEADING_RE.match(line.strip())
            if match:
                headings.append((len(match.group(1)), match.group(2)))
        return headings


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_template(text: str, *, source: Path | None = None) -> Template:
    """Parse template *text* into a :class:`Template`."""
    if not isinstance(text, str):
        raise TypeError("template text must be a string")

    lines = text.splitlines()
    separator = next(
        (index for index, line in enumerate(lines) if line.strip() == SEPARATOR),
        None,
    )
    if separator is None:
        header_lines, body_lines = lines, []
    else:
        header_lines, body_lines = lines[:separator], lines[separator + 1 :]

    raw_options: dict[str, Any] = {}
    header: list[str] = []
    header_started = False

    for raw in header_lines:
        stripped = raw.strip()
        if not stripped:
            if header_started:
                header.append("")
            continue

        # Semicolon comments are allowed anywhere before the separator.
        if stripped.startswith(";"):
            continue

        if not header_started:
            match = _OPTION_RE.match(stripped)
            if match:
                key = match.group(1).lower()
                value_text = _strip_inline_comment(match.group(2))
                raw_options[key] = _parse_value(value_text)
                continue

        # The first heading/literal line ends the options block.
        header_started = True
        header.append(raw.rstrip())

    while header and not header[0].strip():
        header.pop(0)
    while header and not header[-1].strip():
        header.pop()

    raw_options = _apply_aliases(raw_options)
    known = {
        "name",
        "description",
        "directory",
        "filename",
        "extension",
        "encrypt",
        "autofill",
        "fields",
    }
    name_value = raw_options.get("name")
    if name_value is None or str(name_value).strip() == "":
        name = source.stem if source is not None else "template"
    else:
        name = str(name_value).strip()

    options = TemplateOptions(
        name=name,
        description=str(raw_options.get("description", "") or ""),
        directory=str(raw_options.get("directory", "") or ""),
        filename=str(raw_options.get("filename", DEFAULT_FILENAME) or DEFAULT_FILENAME),
        extension=str(
            raw_options.get("extension", DEFAULT_EXTENSION) or DEFAULT_EXTENSION
        ).lstrip("."),
        encrypt=_coerce_bool(raw_options.get("encrypt", False), "encrypt"),
        autofill=_coerce_bool(raw_options.get("autofill", True), "autofill"),
        fields=_coerce_fields(raw_options.get("fields")),
        extra={key: value for key, value in raw_options.items() if key not in known},
    )

    return Template(
        name=options.name,
        options=options,
        header=header,
        body="\n".join(body_lines),
        source=source,
    )


def load_template(path: str | os.PathLike[str]) -> Template:
    """Read and parse a template file."""
    source = Path(path).expanduser()
    try:
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise TemplateError(f"template not found: {source}") from None
    except OSError as exc:
        raise TemplateError(f"could not read template {source}: {exc}") from exc
    return parse_template(text, source=source)


def _apply_aliases(options: dict[str, Any]) -> dict[str, Any]:
    for alias, canonical in _OPTION_ALIASES.items():
        if alias in options and canonical not in options:
            options[canonical] = options.pop(alias)
    return options


def _coerce_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    raise TemplateError(f"option {name!r} must be true or false, got {value!r}")


def _coerce_fields(value: Any) -> list[FieldSpec]:
    if value is None:
        return []
    items: Iterable[Any]
    if isinstance(value, str):
        items = [item for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        items = [value]

    fields: list[FieldSpec] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if "=" in text:
            name, default = text.split("=", 1)
        elif ":" in text:
            name, default = text.split(":", 1)
        else:
            name, default = text, None
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][\w-]*", name):
            raise TemplateError(f"invalid field name: {name!r}")
        fields.append(FieldSpec(name, default.strip() if default is not None else None))
    return fields


def _strip_inline_comment(text: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quote is not None:
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char in "#;" and (index == 0 or text[index - 1].isspace()):
            return text[:index].rstrip()
    return text.strip()


def _parse_value(text: str) -> Any:
    text = text.strip()
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        return [_parse_value(item) for item in _split_list(text[1:-1])]
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text[1:-1]
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("\\'", "'")
    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _split_list(text: str) -> list[str]:
    items: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    escaped = False
    depth = 0
    for char in text:
        if escaped:
            buffer.append(char)
            escaped = False
            continue
        if quote is not None:
            buffer.append(char)
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            buffer.append(char)
        elif char in "[{(":
            depth += 1
            buffer.append(char)
        elif char in "]})":
            depth -= 1
            buffer.append(char)
        elif char == "," and depth == 0:
            items.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    if buffer:
        items.append("".join(buffer))
    return [item for item in items if item.strip()]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def builtin_templates_dir() -> Path:
    """Directory containing the templates shipped with Curve."""
    return Path(str(importlib.resources.files("curve").joinpath("templates")))


def templates_dir() -> Path:
    """User template directory: ``~/.curve/templates``."""
    return config_dir() / "templates"


def template_dirs(config: Mapping[str, Any] | None = None) -> list[Path]:
    """Return template directories, highest priority first.

    Order: ``<cwd>/.curve/templates``, configured dirs, ``~/.curve/templates``,
    then the built-in templates shipped with the package.
    """
    cfg = read_config() if config is None else config
    dirs: list[Path] = []

    project_dir = Path.cwd() / ".curve" / "templates"
    if project_dir.is_dir():
        dirs.append(project_dir)

    templates_config = cfg.get("templates", {})
    if isinstance(templates_config, Mapping):
        configured = templates_config.get("dirs", []) or []
        if isinstance(configured, str):
            configured = [configured]
        for item in configured:
            path = Path(str(item)).expanduser()
            dirs.append(path if path.is_absolute() else Path.cwd() / path)

    dirs.append(templates_dir())
    dirs.append(builtin_templates_dir())

    result: list[Path] = []
    seen: set[str] = set()
    for directory in dirs:
        key = str(directory.resolve(strict=False))
        if key not in seen:
            seen.add(key)
            result.append(directory)
    return result


def list_templates(
    config: Mapping[str, Any] | None = None,
) -> dict[str, Template]:
    """Load every template found on the search path, keyed by name."""
    found: dict[str, Template] = {}
    for directory in template_dirs(config):
        if not directory.is_dir():
            continue
        for path in _template_files(directory):
            try:
                template = load_template(path)
            except TemplateError:
                continue
            found.setdefault(template.name, template)
    return dict(sorted(found.items()))


def _template_files(directory: Path) -> list[Path]:
    files: list[Path] = []
    for suffix in TEMPLATE_SUFFIXES:
        files.extend(directory.glob(f"*{suffix}"))
    return sorted(set(files))


def resolve_template(
    name: str,
    config: Mapping[str, Any] | None = None,
) -> Template:
    """Find a template by name or path.

    ``name`` may be a direct path (``./daily.ext``), a bare name
    (``daily``), or a file name (``daily.ext``).
    """
    candidate = Path(name).expanduser()
    if candidate.exists():
        return load_template(candidate)

    key = name
    for suffix in TEMPLATE_SUFFIXES:
        if key.endswith(suffix):
            key = key[: -len(suffix)]
            break

    templates = list_templates(config)
    if key in templates:
        return templates[key]

    directory_hint = os.sep in name or (os.altsep is not None and os.altsep in name)
    if not directory_hint:
        for directory in template_dirs(config):
            for suffix in TEMPLATE_SUFFIXES:
                path = directory / f"{key}{suffix}"
                if path.exists():
                    return load_template(path)

    searched = ", ".join(str(directory) for directory in template_dirs(config))
    raise TemplateError(f"template {name!r} not found (looked in: {searched})")


def copy_builtin_templates(
    destination: str | os.PathLike[str] | None = None,
    *,
    force: bool = False,
) -> list[Path]:
    """Copy shipped templates into the user template directory."""
    dest = templates_dir() if destination is None else Path(destination).expanduser()
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    written: list[Path] = []
    for source in _template_files(builtin_templates_dir()):
        target = dest / source.name
        if target.exists() and not force:
            continue
        crypto.write_atomic(
            target,
            source.read_text(encoding="utf-8"),
            overwrite=True,
            mode=0o600,
        )
        written.append(target)
    return written


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def resolve_fields(
    template: Template,
    *,
    provided: Mapping[str, Any] | None = None,
    autofill: bool | None = None,
    interactive: bool = False,
    input_fn: Callable[[str], str] = input,
    now: dt.datetime | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Build the token values for *template*.

    Built-in values are filled automatically when ``autofill`` is true (the
    default comes from the template).  Custom fields are always asked for in
    interactive mode, with their default shown in brackets.
    """
    values = {str(key): str(value) for key, value in (provided or {}).items()}
    moment = now if now is not None else dt.datetime.now()
    auto = template.options.autofill if autofill is None else autofill
    cfg = read_config() if config is None else config
    used = template.used_tokens()
    defaults = _builtin_context(moment, template.name, config=cfg)

    resolved: dict[str, str] = {}
    for name in BUILTIN_FIELDS:
        if name in values:
            resolved[name] = values[name]
        elif name in used and not auto and interactive:
            resolved[name] = _ask(name, defaults[name], input_fn)
        else:
            resolved[name] = defaults[name]

    for spec in template.declared_fields():
        if spec.name in values:
            resolved[spec.name] = values[spec.name]
        elif spec.name not in used:
            resolved[spec.name] = spec.default or ""
        elif interactive:
            resolved[spec.name] = _ask(spec.name, spec.default or "", input_fn)
        else:
            resolved[spec.name] = spec.default or ""

    for name, value in values.items():
        resolved.setdefault(name, value)
    return resolved


def render(
    template: Template,
    context: Mapping[str, Any],
    *,
    body_override: str | None = None,
) -> str:
    """Render *template* with *context* into an entry string."""
    header = _render_header(template, context)
    body = template.body if body_override is None else body_override
    body = _substitute(body, context)

    parts: list[str] = []
    if header.strip():
        parts.append(header.strip("\n"))
    if body.strip():
        parts.append(body.strip("\n"))
    text = "\n\n".join(parts)
    return text.rstrip("\n") + "\n" if text.strip() else ""


def _render_header(template: Template, context: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for raw in template.header:
        match = _HEADING_RE.match(raw.strip())
        if match:
            hashes, name = match.groups()
            if name in BUILTIN_FIELDS or name in context:
                value = str(context.get(name, ""))
                if value:
                    lines.append(f"{hashes} {value}")
            else:
                lines.append(raw)
        else:
            lines.append(_substitute(raw, context))
    return "\n".join(lines)


def _substitute(text: str, context: Mapping[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(context[name]) if name in context else match.group(0)

    return _PLACEHOLDER_RE.sub(replace, text)


def _ask(name: str, default: str, input_fn: Callable[[str], str]) -> str:
    label = name.replace("_", " ").replace("-", " ").title()
    prompt = label
    if default:
        prompt += f" [{default}]"
    prompt += ": "
    try:
        raw = input_fn(prompt)
    except (EOFError, KeyboardInterrupt):
        raise TemplateError("input cancelled") from None
    raw = raw.strip()
    return raw or default


def _builtin_context(
    now: dt.datetime,
    template_name: str,
    *,
    config: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    start = _journey_start(config)
    if start is not None:
        day_number = (now.date() - start).days + 1
    else:
        day_number = now.timetuple().tm_yday
    return {
        "day": f"Day {day_number}",
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "clock": now.strftime("%H%M"),
        "datetime": now.strftime("%Y-%m-%d %H:%M"),
        "weekday": now.strftime("%A"),
        "week": str(now.isocalendar().week),
        "month": now.strftime("%B %Y"),
        "year": now.strftime("%Y"),
        "timestamp": now.isoformat(timespec="seconds"),
        "template": template_name,
        "user": getpass.getuser(),
        "host": socket.gethostname(),
    }


def _journey_start(config: Mapping[str, Any] | None) -> dt.date | None:
    if config is None:
        config = read_config()
    journey = config.get("journey", {})
    if not isinstance(journey, Mapping):
        return None
    raw = journey.get("start")
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    if isinstance(raw, str):
        try:
            return dt.date.fromisoformat(raw)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Writing entries
# ---------------------------------------------------------------------------


def entry_path(
    template: Template,
    context: Mapping[str, Any],
    *,
    out: str | os.PathLike[str] | None = None,
    encrypted: bool = False,
    unique: bool = True,
    config: Mapping[str, Any] | None = None,
) -> Path:
    """Compute the output path for a rendered entry."""
    if out is not None:
        path = Path(out).expanduser()
        if encrypted and path.suffix != ".enc":
            path = path.with_name(path.name + ".enc")
        return _unique_path(path) if unique else path

    directory = _resolve_directory(template.options.directory, config)
    pattern = template.options.filename or DEFAULT_FILENAME
    name = _substitute(pattern, context).strip() or _substitute(
        DEFAULT_FILENAME, context
    )
    extension = (template.options.extension or DEFAULT_EXTENSION).lstrip(".")
    if not Path(name).suffix and extension:
        name = f"{name}.{extension}"
    if encrypted:
        name = f"{name}.enc"

    path = directory / name
    return _unique_path(path) if unique else path


def write_entry(
    text: str,
    path: str | os.PathLike[str],
    *,
    encrypted: bool = False,
    password: str | None = None,
    overwrite: bool = False,
    mode: int = 0o600,
) -> Path:
    """Write *text* to *path*, encrypting it first when requested."""
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload: str | bytes = text
    if encrypted:
        if password is None:
            raise TemplateError("a password is required for encrypted entries")
        payload = crypto.encrypt(text, password)
    return crypto.write_atomic(
        destination,
        payload,
        overwrite=overwrite,
        mode=mode,
    )


def get_password(*, confirm: bool = False) -> str:
    """Read an encryption password from the environment or the terminal."""
    from_env = os.environ.get("CURVE_PASSWORD")
    if from_env:
        return from_env
    if not os.isatty(0):
        raise TemplateError(
            "no terminal available for a password; set CURVE_PASSWORD instead"
        )
    password = getpass.getpass("Password: ")
    if confirm:
        again = getpass.getpass("Confirm password: ")
        if password != again:
            raise TemplateError("passwords do not match")
    if not password:
        raise TemplateError("password cannot be empty")
    return password


def _resolve_directory(
    value: str,
    config: Mapping[str, Any] | None,
) -> Path:
    if value:
        path = Path(value).expanduser()
        return path if path.is_absolute() else config_dir() / path

    cfg = read_config() if config is None else config
    entries = cfg.get("entries", {})
    raw = "entries"
    if isinstance(entries, Mapping):
        raw = str(entries.get("dir", raw) or raw)
    path = Path(raw).expanduser()
    return path if path.is_absolute() else config_dir() / path


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    suffix_length = len("".join(path.suffixes))
    stem = path.name[: len(path.name) - suffix_length] if suffix_length else path.name
    suffix = path.name[len(stem) :]
    for index in range(1, 10_000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise TemplateError(f"could not find a free name for {path}")
