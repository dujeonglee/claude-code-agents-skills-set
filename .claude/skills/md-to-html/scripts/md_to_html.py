#!/usr/bin/env python3
"""Convert markdown to standalone HTML with styling.

Usage:
    python md_to_html.py input.md output.html [--title "My Title"] [--theme light|dark]
"""

import sys
import re
import html
import argparse
from typing import Optional
from pathlib import Path

try:
    import markdown
    from markdown.extensions.codehilite import CodeHiliteExtension
    from pygments.formatters import HtmlFormatter
except ImportError as e:
    print(f"Error: Missing dependency — {e}")
    print("Install with: pip install markdown pygments")
    sys.exit(1)


def extract_title(md_text: str, fallback: str) -> str:
    """Extract first H1 heading from markdown, or use fallback."""
    match = re.search(r'^#\s+(.+)', md_text, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def build_pygments_css() -> str:
    """Generate pygments syntax highlighting CSS."""
    return HtmlFormatter(style='friendly').get_style_defs('.codehilite')


DARK_THEME = """
  body { background: #1e1e1e; color: #d4d4d4; }
  h2 { color: #4fc3f7; border-bottom-color: #444; }
  h3 { color: #81d4fa; }
  h4 { color: #aaa; }
  th { background: #2d2d2d; }
  tr:nth-child(even) { background: #252525; }
  th, td { border-color: #444; }
  code { background: #2d2d2d; color: #ce9178; }
  pre { background: #1e1e1e; border-color: #444; }
  blockquote { border-left-color: #555; color: #aaa; }
  hr { border-top-color: #444; }
"""


def convert(input_path: Path, output_path: Path, title: Optional[str], theme: str) -> None:
    md_text = input_path.read_text(encoding='utf-8')

    # Auto-derive title
    page_title = title or extract_title(md_text, fallback=input_path.stem.replace('-', ' ').replace('_', ' ').title())

    # Build markdown instance to access toc after conversion
    md = markdown.Markdown(
        extensions=[
            'tables',
            'fenced_code',
            'toc',
            CodeHiliteExtension(linenums=False, guess_lang=True),
        ],
        output_format='html5',
    )

    html_body = md.convert(md_text)
    toc_html = getattr(md, 'toc', '')

    # Only inject TOC if there are actual entries
    toc_block = ''
    if toc_html and '<li>' in toc_html:
        toc_block = f'<nav class="toc"><h2>Table of Contents</h2>{toc_html}</nav>\n'

    pygments_css = build_pygments_css()
    dark_css = DARK_THEME if theme == 'dark' else ''

    # Auto-create output directory if needed
    output_path.parent.mkdir(parents=True, exist_ok=True)

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(page_title)}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    line-height: 1.6;
    max-width: 960px;
    margin: 40px auto;
    padding: 0 20px;
    color: #1a1a1a;
    background: #fff;
  }}
  h1 {{
    font-size: 1.8em;
    border-bottom: 2px solid #333;
    padding-bottom: 10px;
    margin-top: 40px;
  }}
  h2 {{
    font-size: 1.5em;
    border-bottom: 1px solid #ccc;
    padding-bottom: 6px;
    margin-top: 36px;
    color: #003366;
  }}
  h3 {{
    font-size: 1.2em;
    margin-top: 28px;
    color: #004488;
  }}
  h4 {{
    font-size: 1.05em;
    margin-top: 20px;
    color: #555;
  }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 16px 0;
    font-size: 0.92em;
  }}
  th, td {{
    border: 1px solid #ccc;
    padding: 8px 12px;
    text-align: left;
  }}
  th {{
    background: #f0f4f8;
    font-weight: 600;
  }}
  tr:nth-child(even) {{
    background: #fafafa;
  }}
  code {{
    background: #f4f4f4;
    padding: 2px 5px;
    border-radius: 3px;
    font-size: 0.9em;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  }}
  pre {{
    background: #f6f8fa;
    border: 1px solid #e1e4e8;
    border-radius: 6px;
    padding: 16px;
    overflow-x: auto;
    font-size: 0.85em;
    line-height: 1.45;
  }}
  pre code {{
    background: none;
    padding: 0;
    font-size: inherit;
  }}
  hr {{
    border: none;
    border-top: 1px solid #ddd;
    margin: 32px 0;
  }}
  strong {{ color: #1a1a1a; }}
  blockquote {{
    border-left: 4px solid #dfe2e5;
    margin: 16px 0;
    padding: 0 16px;
    color: #555;
  }}
  ul, ol {{ padding-left: 24px; }}
  li {{ margin: 4px 0; }}
  nav.toc {{
    background: #f8f9fa;
    border: 1px solid #e1e4e8;
    border-radius: 6px;
    padding: 12px 20px;
    margin-bottom: 32px;
    font-size: 0.92em;
  }}
  nav.toc h2 {{
    margin-top: 0;
    font-size: 1em;
    border: none;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  nav.toc ul {{ margin: 0; padding-left: 18px; }}
  nav.toc li {{ margin: 2px 0; }}
  @media print {{
    body {{ max-width: 100%; margin: 0; font-size: 11pt; }}
    nav.toc {{ display: none; }}
    pre {{ white-space: pre-wrap; word-wrap: break-word; }}
    h2 {{ page-break-before: auto; }}
    table, pre, img {{ page-break-inside: avoid; }}
  }}
  {dark_css}
  {pygments_css}
</style>
</head>
<body>
{toc_block}{html_body}
</body>
</html>
"""

    output_path.write_text(html_doc, encoding='utf-8')
    print(f"Generated: {output_path}  (title: \"{page_title}\")")


def main():
    parser = argparse.ArgumentParser(
        description='Convert a Markdown file to a standalone styled HTML document.'
    )
    parser.add_argument('input',  help='Input .md file')
    parser.add_argument('output', help='Output .html file')
    parser.add_argument('--title', default=None,
                        help='Page title (default: auto-detected from first H1)')
    parser.add_argument('--theme', choices=['light', 'dark'], default='light',
                        help='Color theme (default: light)')

    args = parser.parse_args()

    input_path  = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)
    if input_path.suffix.lower() not in ('.md', '.markdown'):
        print(f"Warning: Input file does not look like Markdown: {input_path}")

    convert(input_path, output_path, title=args.title, theme=args.theme)


if __name__ == '__main__':
    main()
