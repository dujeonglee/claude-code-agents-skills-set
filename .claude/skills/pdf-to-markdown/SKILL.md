---
name: pdf-to-markdown
description: Convert PDF files to well-structured Markdown documents. Extracts text with heading detection, optional image extraction, and page range selection. Use this skill whenever the user wants to convert, extract, or transform any PDF into Markdown — including research papers, technical docs, reports, manuals, or datasheets. Trigger even when the user says things like "extract text from this PDF", "convert PDF to md", "turn this PDF into markdown", or "get the content from this PDF".
---

# pdf-to-markdown

Convert a PDF file into a clean, well-structured Markdown document — preserving headings, paragraphs, and optionally images.

## Features

- **Heading detection** — uses font-size heuristics to convert large text into `#`, `##`, `###` headings
- **Image extraction** — optionally saves embedded images alongside the Markdown file
- **Page range selection** — convert specific pages instead of the entire document
- **Page separators** — horizontal rules (`---`) between pages for clarity
- **Clean output** — normalizes whitespace, removes artifacts, produces readable Markdown
- **Safe output** — auto-creates output directory if it doesn't exist

## Dependencies

```
pip install pymupdf
```

## Script location

```
scripts/pdf_to_markdown.py
```

## Usage

### Basic

```bash
python scripts/pdf_to_markdown.py input.pdf output.md
```

### With options

```bash
# Extract images alongside the markdown
python scripts/pdf_to_markdown.py input.pdf output.md --images

# Convert only pages 1 through 5
python scripts/pdf_to_markdown.py input.pdf output.md --pages "1-5"

# Convert specific pages
python scripts/pdf_to_markdown.py input.pdf output.md --pages "1,3,7-10"

# Plain text extraction without heading detection
python scripts/pdf_to_markdown.py input.pdf output.md --no-formatting

# Combined options
python scripts/pdf_to_markdown.py input.pdf output.md --images --pages "1-20"
```

## Claude workflow

When a user asks to convert a PDF to Markdown:

1. **Locate input** — use the path the user provided, or search the current working directory for `.pdf` files
2. **Run the script** via Bash tool:
   ```bash
   python .claude/skills/pdf-to-markdown/scripts/pdf_to_markdown.py <input.pdf> <output>.md [--images] [--pages "..."] [--no-formatting]
   ```
   Place the output alongside the input file unless the user specifies a different location.
3. **Report** — show the output file path and number of pages converted

### Choosing options

| Situation | Recommendation |
|---|---|
| User doesn't mention images | Skip `--images` (text-only is faster and cleaner) |
| User wants figures or diagrams preserved | Use `--images` |
| User wants only specific pages | Use `--pages "1-5"` with the requested range |
| PDF has complex multi-column layout | Consider `--no-formatting` for cleaner raw text |
| User mentions a large PDF (100+ pages) | Suggest `--pages` to process in smaller batches |

### Post-conversion improvements

After running the script, Claude may optionally:
- Review the output and fix obvious formatting issues (broken tables, merged lines)
- Add or adjust heading levels if the auto-detection was imperfect
- Clean up OCR artifacts if the PDF was scanned

## Error cases

| Error | Cause | Fix |
|---|---|---|
| `Input file not found` | Wrong path | Verify the file path and confirm filename |
| `Missing dependency — pymupdf` | PyMuPDF not installed | `pip install pymupdf` |
| `Warning: not a PDF` | Non-.pdf extension | Check if the file is actually a PDF |
| Poor text extraction | Scanned/image-based PDF | The PDF may need OCR preprocessing (e.g., `ocrmypdf`) |
