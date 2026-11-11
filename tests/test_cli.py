from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from curve.main import (
    _editor_command,
    _template_arg,
    build_parser,
    cmd_new,
    cmd_write,
)
from curve.template import TemplateError, parse_template


class EditorTests(unittest.TestCase):
    def test_zed_is_supported_with_wait(self) -> None:
        env = {"VISUAL": "zed", "EDITOR": "zed", "CURVE_EDITOR": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(_editor_command(), ["zed", "--wait"])

    def test_vscode_is_supported_with_wait(self) -> None:
        env = {"VISUAL": "code", "EDITOR": "code", "CURVE_EDITOR": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(_editor_command(), ["code", "--wait"])

    def test_wait_flag_is_not_duplicated(self) -> None:
        env = {"VISUAL": "code --wait", "EDITOR": "code", "CURVE_EDITOR": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(_editor_command(), ["code", "--wait"])

    def test_terminal_editor_is_used_when_configured(self) -> None:
        env = {"VISUAL": "nano", "EDITOR": "nano", "CURVE_EDITOR": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(_editor_command(), ["nano"])

    def test_curve_editor_override_wins(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"VISUAL": "nano", "EDITOR": "nano", "CURVE_EDITOR": "vim -u NONE"},
            clear=False,
        ):
            self.assertEqual(_editor_command(), ["vim", "-u", "NONE"])

    def test_curve_editor_zed_gets_wait(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"VISUAL": "nano", "EDITOR": "nano", "CURVE_EDITOR": "zed"},
            clear=False,
        ):
            self.assertEqual(_editor_command(), ["zed", "--wait"])


class TemplateArgTests(unittest.TestCase):
    def test_template_flag(self) -> None:
        args = build_parser().parse_args(["write", "--template", "personal"])
        self.assertEqual(_template_arg(args), "personal")

    def test_positional_name(self) -> None:
        args = build_parser().parse_args(["write", "personal"])
        self.assertEqual(_template_arg(args), "personal")

    def test_conflicting_names_fail(self) -> None:
        args = build_parser().parse_args(
            ["write", "personal", "--template", "commit"]
        )
        with self.assertRaises(TemplateError):
            _template_arg(args)


class EntryOrderTests(unittest.TestCase):
    template = parse_template('name = "t"\n--\nbody\n')

    def test_open_creates_file_before_editing(self) -> None:
        args = build_parser().parse_args(["new", "--open", "--no-input"])
        calls: list[str] = []
        with mock.patch(
            "curve.main._prepare",
            return_value=(self.template, {}, "draft\n", False),
        ), mock.patch(
            "curve.main.entry_path", return_value=Path("/tmp/entry.md")
        ), mock.patch(
            "curve.main.write_entry",
            side_effect=lambda text, path, **kwargs: (
                calls.append("write"),
                path,
            )[1],
        ), mock.patch(
            "curve.main._run_editor",
            side_effect=lambda path: calls.append("open"),
        ), mock.patch(
            "curve.main.sys.stdin.isatty", return_value=True
        ), mock.patch(
            "curve.main.c.print"
        ):
            self.assertEqual(cmd_new(args), 0)
        self.assertEqual(calls, ["write", "open"])

    def test_write_edits_draft_before_creating(self) -> None:
        args = build_parser().parse_args(["write", "--no-input"])
        calls: list[str] = []

        def prepare(_parsed_args):
            calls.append("prepare")
            return self.template, {}, "draft\n", False

        def edit(text, *, suffix=".md"):
            calls.append("edit")
            return f"{text}edited\n"

        with mock.patch(
            "curve.main._prepare", side_effect=prepare
        ), mock.patch(
            "curve.main.sys.stdin.isatty", return_value=True
        ), mock.patch(
            "curve.main.entry_path", return_value=Path("/tmp/entry.md")
        ), mock.patch(
            "curve.main._edit_text", side_effect=edit
        ), mock.patch(
            "curve.main.write_entry",
            side_effect=lambda text, path, **kwargs: (
                calls.append("write"),
                path,
            )[1],
        ), mock.patch(
            "curve.main.c.print"
        ):
            self.assertEqual(cmd_write(args), 0)
        self.assertEqual(calls, ["prepare", "edit", "write"])

    def test_encrypted_write_prompts_before_editing(self) -> None:
        args = build_parser().parse_args(
            ["write", "--no-input", "--encrypt"]
        )
        calls: list[str] = []

        def prepare(_parsed_args):
            calls.append("prepare")
            return self.template, {}, "draft\n", True

        def edit(text, *, suffix=".md"):
            calls.append("edit")
            return f"{text}edited\n"

        with mock.patch(
            "curve.main._prepare", side_effect=prepare
        ), mock.patch(
            "curve.main.sys.stdin.isatty", return_value=True
        ), mock.patch(
            "curve.main.entry_path", return_value=Path("/tmp/entry.md.enc")
        ), mock.patch(
            "curve.main.get_password",
            side_effect=lambda **kwargs: calls.append("password") or "secret",
        ), mock.patch(
            "curve.main._edit_text", side_effect=edit
        ), mock.patch(
            "curve.main.write_entry",
            side_effect=lambda text, path, **kwargs: (
                calls.append(("write", kwargs.get("password"))),
                path,
            )[1],
        ), mock.patch(
            "curve.main.c.print"
        ):
            self.assertEqual(cmd_write(args), 0)
        self.assertEqual(
            calls,
            [
                "prepare",
                "password",
                "edit",
                ("write", "secret"),
            ],
        )

    def test_encrypted_open_prompts_then_edits_then_encrypts(self) -> None:
        args = build_parser().parse_args(
            ["write", "--no-input", "--encrypt", "--open"]
        )
        calls: list[str] = []

        def prepare(_parsed_args):
            calls.append("prepare")
            return self.template, {}, "draft\n", True

        def edit(text, *, suffix=".md"):
            calls.append("edit")
            return f"{text}edited\n"

        with mock.patch(
            "curve.main._prepare", side_effect=prepare
        ), mock.patch(
            "curve.main.sys.stdin.isatty", return_value=True
        ), mock.patch(
            "curve.main.entry_path", return_value=Path("/tmp/entry.md.enc")
        ), mock.patch(
            "curve.main.get_password",
            side_effect=lambda **kwargs: calls.append("password") or "secret",
        ), mock.patch(
            "curve.main._edit_text", side_effect=edit
        ), mock.patch(
            "curve.main.write_entry",
            side_effect=lambda text, path, **kwargs: (
                calls.append(("write", text, kwargs.get("password"))),
                path,
            )[1],
        ), mock.patch(
            "curve.main._run_editor",
            side_effect=lambda path: calls.append("open-file"),
        ), mock.patch(
            "curve.main.c.print"
        ):
            self.assertEqual(cmd_write(args), 0)
        self.assertEqual(
            calls,
            [
                "prepare",
                "password",
                "edit",
                ("write", "draft\nedited\n", "secret"),
            ],
        )

    def test_open_and_no_edit_conflict(self) -> None:
        args = build_parser().parse_args(
            ["write", "--open", "--no-edit", "--no-input"]
        )
        with self.assertRaises(TemplateError):
            cmd_write(args)


if __name__ == "__main__":
    unittest.main()
