"""Curve: a terminal-based journey tracker."""

from .config import config_dir, config_path, read_config, write_config
from .template import (
    Template,
    TemplateError,
    entry_path,
    list_templates,
    load_template,
    parse_template,
    render,
    resolve_fields,
    resolve_template,
    write_entry,
)

__version__ = "0.1.0"

__all__ = [
    "Template",
    "TemplateError",
    "config_dir",
    "config_path",
    "entry_path",
    "list_templates",
    "load_template",
    "parse_template",
    "read_config",
    "render",
    "resolve_fields",
    "resolve_template",
    "write_config",
    "write_entry",
]
