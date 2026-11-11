# curve

A terminal-based journey tracker. Write `.ext` templates once, then generate
dated entries from them. Entries can be plain text or encrypted at rest.

## Templates

A template has an optional options header, an optional generated heading
block, and a body. The line `--` separates the header from the body.
Templates use the `.ext` extension; `.cuv` is also accepted when discovering
files.

```ext
; lines starting with ";" are comments
name = "template"
description = "Default daily journey entry"
directory = "entries"
filename = "{date}-{template}"
extension = "md"
encrypt = false
autofill = true

#day
##date
##time
--
## What did I do today?

-
```

That renders to something like:

```md
# Day 42

## 2026-11-11

## 14:05

## What did I do today?

-
```

### Generated headings

A heading with **no space** after the `#` is a token:

- `#day` → `# Day 42`
- `##date` → `## 2026-11-11`
- `##time` → `## 14:05`

A heading **with a space** is literal text:

```ext
# Overview
```

Add or remove headings freely. A custom token such as `##mood` becomes a field
that is prompted for when the entry is created. Remove the line and the
question disappears.

### Body fields

Custom fields can also be declared with the `fields` option and used in the
body with `{name}`:

```ext
fields = ["type=feat", "scope=core", "summary", "details"]
--
{type}({scope}): {summary}

{details}
```

`name=default` gives the field a default value that is shown in brackets and
used when the answer is left blank.

### Built-in tokens

| Token       | Example                                                 |
| ----------- | ------------------------------------------------------- |
| `day`       | `Day 42` (days since `[journey] start`, or day of year) |
| `date`      | `2026-11-11`                                            |
| `time`      | `14:05`                                                 |
| `clock`     | `1405` (safe in filenames)                              |
| `datetime`  | `2026-11-11 14:05`                                      |
| `weekday`   | `Wednesday`                                             |
| `week`      | `46`                                                    |
| `month`     | `November 2026`                                         |
| `year`      | `2026`                                                  |
| `timestamp` | `2026-11-11T14:05:00`                                   |
| `template`  | template name                                           |
| `user`      | current user                                            |
| `host`      | hostname                                                |

### Options

Options are `key = value` lines before the first heading. Values can be
strings (`"..."` or `'...'`), numbers, booleans (`true/false`, `yes/no`,
`on/off`, `1/0`), or lists (`["a", "b"]`).

| Option        | Default              | Meaning                                               |
| ------------- | -------------------- | ----------------------------------------------------- |
| `name`        | file stem            | Template name                                         |
| `description` | empty                | Shown by `curve list`                                 |
| `directory`   | config `entries.dir` | Output directory; relative paths are under `~/.curve` |
| `filename`    | `{date}-{template}`  | Output file name pattern                              |
| `extension`   | `md`                 | Added when the filename has no suffix                 |
| `encrypt`     | `false`              | Encrypt entries at rest (AES-256-GCM)                 |
| `autofill`    | `true`               | Fill date/time tokens silently; `false` asks for them |
| `fields`      | empty                | Body fields to prompt for                             |

Aliases: `dir`, `file`, `ext`, `encrypted`, `auto`, `auto_fill`.
Unknown options are preserved and shown by `curve show`, so you can add your
own metadata without breaking anything.

## Bundled templates

`curve init` copies three templates to `~/.curve/templates`:

- `template.ext` — the default daily entry above.
- `personal.ext` — encrypted journal with `mood`, `energy`, `gratitude`.
- `commit.ext` — conventional commit message with `type`, `scope`,
  `summary`, `details`.

Edit them with `curve edit personal`, or add your own `anything.ext`.

Template search order (first match wins):

1. `./.curve/templates/`
2. directories in `[templates] dirs`
3. `~/.curve/templates/`
4. the templates shipped with Curve

## CLI

```console
curve                          # banner + template list
curve init                     # install bundled templates and config
curve list                     # list templates
curve show personal            # inspect a template
curve show personal --raw      # print the file
curve edit personal            # open in a terminal editor

curve new                      # create from the configured default template
curve new template             # create from a specific template
curve new template -m "today"  # replace the body
curve new commit -f type=fix -f scope=parser -f summary="fix crash"
curve new personal -f mood=good -f energy=4 -f gratitude=coffee
curve new template --at 2026-11-01 --dry-run
curve new template --no-input  # never prompt, use defaults
curve new template --no-autofill
curve new template --open      # create the file, then open it
curve render template          # print instead of writing

curve write                    # write the Markdown draft, then create the entry
curve write personal           # same, for an encrypted entry
curve w --template personal    # same thing with a flag
curve write -t personal --open # ask password, edit, then encrypt
curve write commit -f type=fix -f scope=parser -f summary="fix crash"
curve write template --no-edit # write directly, no editor

curve encrypt notes.md         # -> notes.md.enc
curve decrypt notes.md.enc     # -> notes.md
curve decrypt notes.md.enc --stdout
```

`curve new` will never overwrite an existing entry: it appends `-1`, `-2`,
etc. Use `--out PATH --force` to write an exact path.

`curve write` renders the full Markdown draft to a temporary file, opens it in
the editor, and only creates the real entry after you save and exit. `--open`
writes the entry file first, then opens it.

For encrypted entries, Curve asks for the password **before** opening the
editor, but does not encrypt yet. After you save and exit, it encrypts the
edited Markdown and writes the final `.enc` file. With `--open`, that means no
encrypted file exists while you are still writing.

Use `--no-edit` (or `--body`) to write without opening an editor.

Curve supports terminal editors (`nano`, `vim`, `vi`, `micro`, `hx`, ...) and
GUI editors (`zed`, `code`, `code-insiders`, `codium`). It uses
`$CURVE_EDITOR` when set, then `$VISUAL`/`$EDITOR`, then falls back to
`nano` / `vim` / `vi`.

Zed and VS Code are launched with their `--wait` flag automatically, so Curve
waits until you close the file before creating or re-encrypting the entry.

```console
CURVE_EDITOR=zed curve w --template personal
CURVE_EDITOR=code curve w --template personal
```

## Config

`curve init` creates `~/.curve/config.toml`:

```toml
[templates]
default = "template"

[entries]
dir = "entries"

[journey]
start = "2026-11-11"
```

`journey.start` controls the `day` token. If it is missing, `day` falls back
to the day of the year. `curve config` prints the file and `curve config
--edit` opens it.

## Encryption

Templates with `encrypt = true` ask for a password (or use the
`CURVE_PASSWORD` environment variable) and write an `.enc` file. Decrypt with
`curve decrypt FILE --stdout` to read an entry without creating a plaintext
file on disk.

Passwords are never stored. Encryption uses scrypt for key derivation and
AES-256-GCM for authenticated encryption; the parameters are stored in the
file header.
