"""Command line interface for Curve."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__, encrypt as crypto
from .config import config_path, read_config, write_config
from .template import (
    Template,
    TemplateError,
    copy_builtin_templates,
    entry_path,
    get_password,
    list_templates,
    render,
    resolve_fields,
    resolve_template,
    templates_dir,
    write_entry,
)

c = Console()

BANNER = """
 ██████╗██╗   ██╗██████╗ ██╗   ██╗███████╗
██╔════╝██║   ██║██╔══██╗██║   ██║██╔════╝
██║     ██║   ██║██████╔╝██║   ██║█████╗
██║     ██║   ██║██╔══██╗╚██╗ ██╔╝██╔══╝
╚██████╗╚██████╔╝██║  ██║ ╚████╔╝ ███████╗
 ╚═════╝ ╚═════╝ ╚═╝  ╚═╝  ╚═══╝  ╚══════╝
"""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            c.print(BANNER)
            return _show_templates()
        return args.handler(args) or 0
    except KeyboardInterrupt:
        c.print("\n[dim]cancelled[/dim]")
        return 130
    except (TemplateError, FileExistsError, OSError, ValueError) as exc:
        c.print(f"[bold red]error:[/bold red] {exc}")
        return 1


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    return _show_templates()


def _show_templates() -> int:
    config = read_config()
    templates = list_templates(config)
    if not templates:
        c.print(
            "[yellow]No templates found.[/yellow] "
            "Run [bold]curve init[/bold] to install the bundled ones."
        )
        return 0

    default = _default_template_name(config)
    table = Table(title="Templates", header_style="bold cyan")
    table.add_column("Name")
    table.add_column("Encrypt", justify="center")
    table.add_column("Autofill", justify="center")
    table.add_column("Source")
    table.add_column("Description")
    for name, template in templates.items():
        marker = " [green]*[/green]" if name == default else ""
        table.add_row(
            f"{name}{marker}",
            "yes" if template.options.encrypt else "no",
            "yes" if template.options.autofill else "no",
            str(template.source or ""),
            template.options.description,
        )
    c.print(table)
    if default not in templates:
        c.print(f"[dim]default template {default!r} is not installed[/dim]")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    template = resolve_template(args.name)
    if args.raw and template.source is not None:
        c.print(
            template.source.read_text(encoding="utf-8"),
            markup=False,
            highlight=False,
        )
        return 0

    table = Table(title=f"Template: {template.name}", show_header=False, box=None)
    rows = [
        ("Description", template.options.description or "-"),
        ("Source", str(template.source or "-")),
        ("Directory", template.options.directory or "(config default)"),
        ("Filename", template.options.filename),
        ("Extension", template.options.extension),
        ("Encrypt", "yes" if template.options.encrypt else "no"),
        ("Autofill", "yes" if template.options.autofill else "no"),
        (
            "Fields",
            ", ".join(spec.name for spec in template.declared_fields()) or "-",
        ),
        (
            "Headings",
            " ".join(
                f"{'#' * level}{token}"
                for level, token in template.heading_tokens()
            )
            or "-",
        ),
    ]
    if template.options.extra:
        rows.append(
            (
                "Other options",
                ", ".join(
                    f"{key}={value!r}"
                    for key, value in sorted(template.options.extra.items())
                ),
            )
        )
    for label, value in rows:
        table.add_row(f"[bold]{label}[/bold]", value)
    c.print(table)
    c.print(
        Panel(
            Text(template.body or ""),
            title="body",
            border_style="blue",
        )
    )
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    template, context, text, encrypted = _prepare(args)
    return _create_entry(
        args,
        template,
        context,
        text,
        encrypted,
        open_after=bool(args.open or args.edit),
    )


def _create_entry(
    args: argparse.Namespace,
    template: Template,
    context: dict[str, str],
    text: str,
    encrypted: bool,
    *,
    open_after: bool = False,
    path: Path | None = None,
    password: str | None = None,
) -> int:
    if path is None:
        path = entry_path(
            template,
            context,
            out=getattr(args, "out", None),
            encrypted=encrypted,
            unique=not args.force,
        )
    if args.dry_run:
        c.print(
            Panel(
                Text(text),
                title=str(path),
                subtitle="not written",
                border_style="yellow",
            )
        )
        return 0
    if open_after and not sys.stdin.isatty():
        raise TemplateError("--open needs an interactive terminal")

    if encrypted and password is None:
        password = get_password(confirm=not path.exists())

    if open_after and encrypted:
        # Ask for the password before the editor, but only encrypt after the
        # user has saved and closed it.
        edited = _edit_text(text, suffix=f".{template.options.extension}")
        written = write_entry(
            edited,
            path,
            encrypted=True,
            password=password,
            overwrite=args.force,
        )
        c.print(f"[green]created[/green] {written}")
        c.print("[dim]entry is encrypted at rest[/dim]")
        return 0

    written = write_entry(
        text,
        path,
        encrypted=encrypted,
        password=password,
        overwrite=args.force,
    )
    c.print(f"[green]created[/green] {written}")
    if encrypted:
        c.print("[dim]entry is encrypted at rest[/dim]")

    if open_after:
        _run_editor(written)
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    _template, _context, text, _encrypted = _prepare(args)
    sys.stdout.write(text)
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    """Write an entry, using a terminal editor for the Markdown draft."""
    if args.open and args.no_edit:
        raise TemplateError("use either --open or --no-edit, not both")
    if args.no_edit or args.body is not None or not sys.stdin.isatty():
        return cmd_new(args)
    if args.open or args.edit:
        return cmd_new(args)

    template, context, text, encrypted = _prepare(args)
    path = entry_path(
        template,
        context,
        out=getattr(args, "out", None),
        encrypted=encrypted,
        unique=not args.force,
    )
    if args.dry_run:
        return _create_entry(
            args,
            template,
            context,
            text,
            encrypted,
            path=path,
        )

    # Ask for the password before the editor opens, but encrypt only after
    # the edited Markdown has been saved.
    password = get_password(confirm=not path.exists()) if encrypted else None
    text = _edit_text(text, suffix=f".{template.options.extension}")
    return _create_entry(
        args,
        template,
        context,
        text,
        encrypted,
        path=path,
        password=password,
    )


def cmd_edit(args: argparse.Namespace) -> int:
    template = resolve_template(args.name)
    if template.source is None:
        raise TemplateError("this template has no file on disk to edit")
    _run_editor(template.source)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    destination = templates_dir()
    written = copy_builtin_templates(destination, force=args.force)
    if written:
        c.print(f"[green]installed[/green] {len(written)} template(s) in {destination}")
        for path in written:
            c.print(f"  [cyan]{path}[/cyan]")
    else:
        c.print(f"[dim]templates already installed in {destination}[/dim]")

    config = read_config()
    changed = False
    for section, key, value in (
        ("templates", "default", "template"),
        ("entries", "dir", "entries"),
        ("journey", "start", dt.date.today().isoformat()),
    ):
        table = config.get(section)
        if not isinstance(table, dict):
            table = {}
            config[section] = table
        if key not in table:
            table[key] = value
            changed = True
    if changed:
        write_config(config)
        c.print(f"[green]wrote[/green] config {config_path()}")
    else:
        c.print(f"[dim]config already exists at {config_path()}[/dim]")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    path = config_path()
    if args.path:
        c.print(str(path))
        return 0
    if args.edit:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _run_editor(path)
        return 0
    c.print(f"[bold]{path}[/bold]")
    if path.exists():
        c.print(path.read_text(encoding="utf-8"), markup=False, highlight=False)
    else:
        c.print("[dim]not created yet; run `curve init`[/dim]")
    return 0


def cmd_encrypt(args: argparse.Namespace) -> int:
    password = get_password(confirm=True)
    path = crypto.encrypt_file(
        args.path,
        password,
        out=args.out,
        overwrite=args.force,
    )
    c.print(f"[green]encrypted[/green] {path}")
    return 0


def cmd_decrypt(args: argparse.Namespace) -> int:
    password = get_password()
    if args.stdout:
        data = crypto.decrypt(Path(args.path).read_bytes(), password)
        sys.stdout.buffer.write(data)
        if not data.endswith(b"\n"):
            sys.stdout.buffer.write(b"\n")
        return 0
    path = crypto.decrypt_file(
        args.path,
        password,
        out=args.out,
        overwrite=args.force,
    )
    c.print(f"[green]decrypted[/green] {path}")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prepare(
    args: argparse.Namespace,
) -> tuple[Template, dict[str, str], str, bool]:
    name = _template_arg(args) or _default_template_name(read_config())
    template = resolve_template(name)
    provided = _parse_field_args(args.field)
    interactive = sys.stdin.isatty() and not args.no_input
    context = resolve_fields(
        template,
        provided=provided,
        autofill=args.autofill,
        interactive=interactive,
        input_fn=lambda prompt: c.input(prompt, markup=False),
        now=_parse_when(args.at),
    )
    text = render(template, context, body_override=args.body)
    return template, context, text, _resolve_encryption(args, template)


def _default_template_name(config: Mapping[str, object]) -> str:
    section = config.get("templates", {})
    if isinstance(section, Mapping):
        value = section.get("default")
        if value:
            return str(value)
    return "template"


def _template_arg(args: argparse.Namespace) -> str | None:
    flag = getattr(args, "template", None)
    positional = getattr(args, "name", None)
    if flag and positional and flag != positional:
        raise TemplateError(
            "use either the positional template name or --template, not both"
        )
    return flag or positional


def _parse_field_args(pairs: list[str] | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise TemplateError(
                f"invalid --field {pair!r}; expected KEY=VALUE"
            )
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise TemplateError("--field keys cannot be empty")
        values[key] = value
    return values


def _parse_when(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if text.lower() == "now":
        return dt.datetime.now()
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return dt.datetime.combine(dt.date.fromisoformat(text), dt.time())
    except ValueError:
        raise TemplateError(
            f"invalid --at value {raw!r}; use ISO date/time like 2026-11-11 or "
            "'2026-11-11 14:30'"
        ) from None


def _resolve_encryption(args: argparse.Namespace, template: Template) -> bool:
    if args.encrypt is not None:
        return bool(args.encrypt)
    out = getattr(args, "out", None)
    if out is not None and str(out).endswith(".enc"):
        return True
    return template.options.encrypt


def _run_editor(path: Path) -> None:
    editor = _editor_command()
    subprocess.run([*editor, str(path)], check=False)


_TERMINAL_EDITORS = {
    "nano",
    "vim",
    "vi",
    "nvim",
    "micro",
    "hx",
    "helix",
    "kak",
    "ne",
    "mcedit",
    "tilde",
}

_GUI_EDITORS = {
    "zed",
    "code",
    "code-insiders",
    "codium",
}

_WAIT_EDITORS = _GUI_EDITORS


def _editor_command() -> list[str]:
    """Return an editor command, waiting for GUI editors to close."""
    override = os.environ.get("CURVE_EDITOR")
    if override:
        return _ensure_wait(shlex.split(override))
    for variable in ("VISUAL", "EDITOR"):
        value = os.environ.get(variable)
        if not value:
            continue
        command = shlex.split(value)
        if command and Path(command[0]).name in (_TERMINAL_EDITORS | _GUI_EDITORS):
            return _ensure_wait(command)
    for candidate in ("nano", "vim", "vi", "micro", "hx", "nvim"):
        executable = shutil.which(candidate)
        if executable:
            return [executable]
    raise TemplateError(
        "no editor found; install nano or vim, set $CURVE_EDITOR, "
        "or set $VISUAL/$EDITOR to zed or code"
    )


def _ensure_wait(command: list[str]) -> list[str]:
    name = Path(command[0]).name
    if name in _WAIT_EDITORS and "--wait" not in command and "-w" not in command:
        return [*command, "--wait"]
    return command


def _edit_text(text: str, *, suffix: str = ".md") -> str:
    fd, name = tempfile.mkstemp(prefix="curve-", suffix=suffix, text=True)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
        _run_editor(temp)
        return temp.read_text(encoding="utf-8")
    finally:
        temp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="curve",
        description="Terminal journey tracking with reusable .ext templates.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    listing = subparsers.add_parser(
        "list",
        aliases=["ls", "templates"],
        help="list available templates",
    )
    listing.set_defaults(handler=cmd_list)

    show = subparsers.add_parser("show", help="inspect a template")
    show.add_argument("name", help="template name or path")
    show.add_argument("--raw", action="store_true", help="print the file as-is")
    show.set_defaults(handler=cmd_show)

    new = subparsers.add_parser("new", help="create an entry from a template")
    _add_entry_args(new, writing=True)
    new.set_defaults(handler=cmd_new)

    write = subparsers.add_parser(
        "write",
        aliases=["w"],
        help="write a new entry in a terminal editor",
    )
    _add_entry_args(write, writing=True)
    write.add_argument(
        "--no-edit",
        action="store_true",
        help="write directly without opening an editor",
    )
    write.set_defaults(handler=cmd_write)

    render_parser = subparsers.add_parser(
        "render",
        help="render an entry to stdout without writing it",
    )
    _add_entry_args(render_parser, writing=False)
    render_parser.set_defaults(handler=cmd_render)

    edit = subparsers.add_parser("edit", help="open a template in a terminal editor")
    edit.add_argument("name", help="template name or path")
    edit.set_defaults(handler=cmd_edit)

    init = subparsers.add_parser(
        "init",
        help="install bundled templates and a starter config",
    )
    init.add_argument("--force", action="store_true", help="overwrite templates")
    init.set_defaults(handler=cmd_init)

    config = subparsers.add_parser("config", help="show or edit the config")
    config.add_argument("--path", action="store_true", help="print the path only")
    config.add_argument("--edit", action="store_true", help="open in a terminal editor")
    config.set_defaults(handler=cmd_config)

    encrypt = subparsers.add_parser("encrypt", help="encrypt a file")
    encrypt.add_argument("path")
    encrypt.add_argument("--out", "-o")
    encrypt.add_argument("--force", action="store_true")
    encrypt.set_defaults(handler=cmd_encrypt)

    decrypt = subparsers.add_parser("decrypt", help="decrypt a file")
    decrypt.add_argument("path")
    decrypt.add_argument("--out", "-o")
    decrypt.add_argument("--stdout", action="store_true", help="print instead of writing")
    decrypt.add_argument("--force", action="store_true")
    decrypt.set_defaults(handler=cmd_decrypt)

    return parser


def _add_entry_args(parser: argparse.ArgumentParser, *, writing: bool) -> None:
    parser.add_argument(
        "name",
        nargs="?",
        help="template name; defaults to the configured template",
    )
    parser.add_argument(
        "--template",
        "-t",
        metavar="NAME",
        help="template name (same as the positional NAME)",
    )
    parser.add_argument(
        "--field",
        "-f",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="supply a field value (repeatable)",
    )
    parser.add_argument(
        "--body",
        "-m",
        default=None,
        help="replace the template body with this text",
    )
    parser.add_argument(
        "--at",
        metavar="WHEN",
        help="use a specific ISO date/time instead of now",
    )
    parser.add_argument(
        "--no-input",
        "-y",
        action="store_true",
        help="never prompt; use defaults for blank fields",
    )
    autofill = parser.add_mutually_exclusive_group()
    autofill.add_argument(
        "--autofill",
        dest="autofill",
        action="store_true",
        default=None,
        help="fill date/time tokens automatically",
    )
    autofill.add_argument(
        "--no-autofill",
        dest="autofill",
        action="store_false",
        help="ask for date/time tokens too",
    )
    encryption = parser.add_mutually_exclusive_group()
    encryption.add_argument(
        "--encrypt",
        dest="encrypt",
        action="store_true",
        default=None,
        help="force encryption",
    )
    encryption.add_argument(
        "--no-encrypt",
        dest="encrypt",
        action="store_false",
        help="force plain text",
    )
    if writing:
        parser.add_argument("--out", "-o", help="exact output path")
        parser.add_argument(
            "--force",
            action="store_true",
            help="overwrite an existing output file",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="show what would be written",
        )
        parser.add_argument(
            "--open",
            action="store_true",
            help=(
                "open the entry in a terminal editor; encrypted entries are "
                "written after you save"
            ),
        )
        parser.add_argument(
            "--edit",
            action="store_true",
            help=argparse.SUPPRESS,
        )


if __name__ == "__main__":
    raise SystemExit(main())
