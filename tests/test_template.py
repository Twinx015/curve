from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from curve.config import config_dir
from curve.template import (
    TemplateError,
    entry_path,
    list_templates,
    parse_template,
    render,
    resolve_fields,
    resolve_template,
)


SAMPLE = """
; a comment
name = "daily"
description = "Daily note"
encrypt = true
autofill = true
fields = ["mood", "energy=3"]

#day
##date
##mood
--
## Notes

mood: {mood}
energy: {energy}
"""


class ParseTests(unittest.TestCase):
    def test_options_header_and_body(self) -> None:
        template = parse_template(SAMPLE, source=Path("daily.ext"))
        self.assertEqual(template.name, "daily")
        self.assertTrue(template.options.encrypt)
        self.assertTrue(template.options.autofill)
        self.assertEqual(template.header, ["#day", "##date", "##mood"])
        self.assertIn("## Notes", template.body)
        self.assertEqual(
            [spec.name for spec in template.declared_fields()], ["mood", "energy"]
        )

    def test_literal_heading_and_aliases(self) -> None:
        template = parse_template(
            """
dir = "notes"
file = "entry-{date}"
ext = "txt"
auto = false
encrypted = yes
# Overview
#date
--
body
""",
            source=Path("thing.ext"),
        )
        self.assertEqual(template.options.directory, "notes")
        self.assertEqual(template.options.filename, "entry-{date}")
        self.assertEqual(template.options.extension, "txt")
        self.assertFalse(template.options.autofill)
        self.assertTrue(template.options.encrypt)
        self.assertEqual(template.header, ["# Overview", "#date"])

    def test_unknown_options_are_kept(self) -> None:
        template = parse_template(
            """
name = "x"
tags = ["one", "two"]
--
body
"""
        )
        self.assertEqual(template.options.extra, {"tags": ["one", "two"]})

    def test_invalid_bool(self) -> None:
        with self.assertRaises(TemplateError):
            parse_template("encrypt = maybe\n--\nbody\n")

    def test_separator_is_optional(self) -> None:
        template = parse_template("name = \"x\"\n")
        self.assertEqual(template.body, "")
        self.assertEqual(template.header, [])


class RenderTests(unittest.TestCase):
    def test_builtins_and_custom_fields(self) -> None:
        template = parse_template(SAMPLE, source=Path("daily.ext"))
        context = resolve_fields(
            template,
            provided={"mood": "good", "energy": "4"},
            interactive=False,
            now=__import__("datetime").datetime(2026, 11, 11, 9, 30),
            config={"journey": {"start": "2026-11-10"}},
        )
        output = render(template, context)
        self.assertIn("# Day 2", output)
        self.assertIn("## 2026-11-11", output)
        self.assertIn("## good", output)
        self.assertIn("mood: good", output)
        self.assertIn("energy: 4", output)
        self.assertNotIn("--", output)

    def test_interactive_defaults(self) -> None:
        template = parse_template(
            "autofill = true\nfields = [\"mood=calm\"]\n--\n{mood}\n"
        )
        asked: list[str] = []

        def answer(prompt: str) -> str:
            asked.append(prompt)
            return ""

        context = resolve_fields(template, interactive=True, input_fn=answer)
        self.assertEqual(context["mood"], "calm")
        self.assertEqual(len(asked), 1)
        self.assertIn("[calm]", asked[0])
        self.assertEqual(render(template, context), "calm\n")

    def test_body_override(self) -> None:
        template = parse_template("--\noriginal {date}\n")
        context = resolve_fields(
            template,
            provided={"date": "2026-01-02"},
            interactive=False,
        )
        self.assertEqual(
            render(template, context, body_override="new {date}\n"),
            "new 2026-01-02\n",
        )


class DiscoveryTests(unittest.TestCase):
    def test_both_ext_and_cuv_are_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"CURVE_HOME": tmp}
        ):
            templates = Path(tmp) / "templates"
            templates.mkdir()
            (templates / "one.ext").write_text(
                'name = "one"\n--\nbody\n', encoding="utf-8"
            )
            (templates / "two.cuv").write_text(
                'name = "two"\n--\nbody\n', encoding="utf-8"
            )
            config = {"templates": {"dirs": [str(templates)]}}
            found = list_templates(config)
            self.assertIn("one", found)
            self.assertIn("two", found)
            self.assertEqual(resolve_template("two", config).name, "two")


class PathTests(unittest.TestCase):
    def test_entry_path_uses_template_and_config(self) -> None:
        template = parse_template(
            'directory = "journal"\nfilename = "{date}-{template}"\n--\nx\n'
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"CURVE_HOME": tmp}
        ):
            context = {"date": "2026-11-11", "template": "daily"}
            path = entry_path(template, context, config={})
            self.assertEqual(
                path,
                Path(tmp) / "journal" / "2026-11-11-daily.md",
            )

    def test_encrypted_output_gets_enc_suffix_and_unique_name(self) -> None:
        template = parse_template('filename = "note"\n--\nx\n')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"CURVE_HOME": tmp}
        ):
            directory = config_dir() / "entries"
            directory.mkdir(parents=True)
            (directory / "note.md.enc").write_bytes(b"x")
            path = entry_path(
                template,
                {},
                encrypted=True,
                config={"entries": {"dir": "entries"}},
            )
            self.assertEqual(path.name, "note-1.md.enc")


if __name__ == "__main__":
    unittest.main()
