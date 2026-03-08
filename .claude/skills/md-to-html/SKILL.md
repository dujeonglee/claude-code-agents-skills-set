---
name: md-to-html
description: Convert Markdown files to polished, standalone HTML documents with syntax highlighting, auto-generated TOC, and light/dark themes. Use this skill whenever the user wants to render, export, publish, or convert any .md or Markdown content into an HTML file — including technical docs, reports, README files, kernel/driver documentation, or any structured Markdown. Trigger even when the user says things like "make this readable in a browser", "export as HTML", "generate a doc from this markdown", or "turn this into a nice HTML page".
---

# md-to-html

Convert a Markdown file into a self-contained, styled HTML document — no external dependencies, no build tools, just a single `.html` file ready to open in a browser or share.

## Features

- **Auto title** — extracted from the first `# H1` heading; filename used as fallback
- **TOC** — auto-generated and injected as a styled `<nav>` block (hidden on print)
- **Syntax highlighting** — Pygments-powered, language auto-detected (great for C/kernel code)
- **Table styling** — clean borders, alternating row shading
- **Light / dark theme** — `--theme dark` for dark background
- **Print-friendly** — `@media print` hides TOC, wraps long lines, avoids page breaks inside tables/code
- **Safe output** — auto-creates output directory if it doesn't exist

## Dependencies

```
pip install markdown pygments
```

Both are typically pre-installed in team environments.

## Script location

```
scripts/md_to_html.py
```

## Usage

### Basic

```bash
python scripts/md_to_html.py input.md output.html
```

### With options

```bash
# Override auto-detected title
python scripts/md_to_html.py input.md output.html --title "WLAN RX Data Path"

# Dark theme
python scripts/md_to_html.py input.md output.html --theme dark

# Both
python scripts/md_to_html.py input.md output.html --title "Driver Internals" --theme dark
```

## Claude workflow

When a user asks to convert Markdown to HTML:

1. **Locate input** — use the path the user provided, or search the current working directory for `.md` files
2. **Run the script** via Bash tool:
   ```bash
   python .claude/skills/md-to-html/scripts/md_to_html.py <input> <output>.html [--title "..."] [--theme light|dark]
   ```
   Place the output alongside the input file unless the user specifies a different location.
3. **Report** — show the output file path so the user can open it in a browser

### Choosing options

| Situation | Recommendation |
|---|---|
| User doesn't specify title | Let auto-detection handle it |
| User pastes or names a title | Pass via `--title` |
| User mentions dark mode / dark theme | Use `--theme dark` |
| Technical / kernel / driver docs | Default light; dark is optional |

## Error cases

| Error | Cause | Fix |
|---|---|---|
| `Input file not found` | Wrong path | Verify the file path and confirm filename |
| `Missing dependency` | `markdown` or `pygments` not installed | `pip install markdown pygments` |
| `Warning: not Markdown` | Non-.md extension | Safe to proceed; script still runs |
