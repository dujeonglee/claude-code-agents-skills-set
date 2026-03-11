#!/usr/bin/env python3
"""Convert a PDF file to Markdown.

Extracts text, tables, and optionally images from a PDF document
and produces a well-structured Markdown file.

Usage:
    python pdf_to_markdown.py input.pdf output.md [options]
"""

import sys
import re
import argparse
from pathlib import Path

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        print("Error: Missing dependency — pymupdf")
        print("Install with: pip install pymupdf")
        sys.exit(1)


def extract_images(page, page_num: int, output_dir: Path) -> list[str]:
    """Extract images from a page and save them to output_dir.

    Returns a list of markdown image references.
    """
    images_dir = output_dir / "images"
    image_refs = []

    for img_index, img in enumerate(page.get_images(full=True)):
        xref = img[0]
        try:
            base_image = page.parent.extract_image(xref)
        except Exception:
            continue

        if not base_image or not base_image.get("image"):
            continue

        ext = base_image.get("ext", "png")
        filename = f"page{page_num + 1}_img{img_index + 1}.{ext}"
        filepath = images_dir / filename

        images_dir.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(base_image["image"])

        image_refs.append(f"![Image from page {page_num + 1}](images/{filename})")

    return image_refs


def clean_text(text: str) -> str:
    """Clean extracted text: normalize whitespace, fix common artifacts."""
    # Collapse multiple blank lines into two newlines (one blank line)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Remove trailing whitespace on each line
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
    return text.strip()


def detect_heading(line: str, font_size: float, max_font: float) -> str:
    """Heuristically convert a line to a markdown heading based on font size.

    Uses relative font size compared to the largest font on the page.
    """
    stripped = line.strip()
    if not stripped:
        return line

    # Only consider short lines as potential headings (< 120 chars)
    if len(stripped) > 120:
        return line

    if font_size >= max_font * 0.95:
        return f"# {stripped}"
    elif font_size >= max_font * 0.75:
        return f"## {stripped}"
    elif font_size >= max_font * 0.6:
        return f"### {stripped}"

    return line


def extract_page_with_formatting(page) -> str:
    """Extract text from a page using font-size info for heading detection."""
    blocks = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)["blocks"]

    max_font = 0.0
    lines_with_size = []

    # First pass: collect lines and find max font size
    for block in blocks:
        if block["type"] != 0:  # text block
            continue
        for bline in block["lines"]:
            text_parts = []
            line_font_size = 0.0
            for span in bline["spans"]:
                text_parts.append(span["text"])
                if span["size"] > line_font_size:
                    line_font_size = span["size"]
            line_text = "".join(text_parts).rstrip()
            if line_text.strip():
                lines_with_size.append((line_text, line_font_size))
                if line_font_size > max_font:
                    max_font = line_font_size

    if max_font == 0:
        return page.get_text("text")

    # Second pass: apply heading detection
    result_lines = []
    for line_text, font_size in lines_with_size:
        result_lines.append(detect_heading(line_text, font_size, max_font))

    return "\n".join(result_lines)


def convert_pdf_to_markdown(
    input_path: Path,
    output_path: Path,
    extract_imgs: bool = False,
    use_formatting: bool = True,
    pages: str | None = None,
) -> None:
    """Convert a PDF file to Markdown.

    Args:
        input_path: Path to the input PDF file.
        output_path: Path for the output Markdown file.
        extract_imgs: Whether to extract and save images.
        use_formatting: Whether to detect headings from font sizes.
        pages: Optional page range string (e.g., "1-5", "1,3,5", "2-").
    """
    doc = pymupdf.open(str(input_path))
    total_pages = len(doc)

    # Parse page range
    page_indices = parse_page_range(pages, total_pages) if pages else range(total_pages)

    md_parts = []
    output_dir = output_path.parent

    for page_num in page_indices:
        if page_num < 0 or page_num >= total_pages:
            print(f"Warning: Skipping out-of-range page {page_num + 1}")
            continue

        page = doc[page_num]

        # Extract text
        if use_formatting:
            text = extract_page_with_formatting(page)
        else:
            text = page.get_text("text")

        text = clean_text(text)

        if text:
            md_parts.append(text)

        # Extract images if requested
        if extract_imgs:
            image_refs = extract_images(page, page_num, output_dir)
            if image_refs:
                md_parts.append("\n".join(image_refs))

    doc.close()

    # Combine all pages
    markdown_content = "\n\n---\n\n".join(md_parts)
    markdown_content = clean_text(markdown_content)

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown_content + "\n", encoding="utf-8")

    page_count = len(list(page_indices)) if not isinstance(page_indices, range) else len(page_indices)
    print(f"Generated: {output_path}  ({page_count} pages converted)")


def parse_page_range(page_range: str, total_pages: int) -> list[int]:
    """Parse a page range string into a list of 0-based page indices.

    Supported formats:
        "3"       -> [2]
        "1-5"     -> [0, 1, 2, 3, 4]
        "2-"      -> [1, 2, ..., last]
        "1,3,5"   -> [0, 2, 4]
        "1-3,7-9" -> [0, 1, 2, 6, 7, 8]
    """
    indices = []
    for part in page_range.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start = int(start) if start.strip() else 1
            end = int(end) if end.strip() else total_pages
            indices.extend(range(start - 1, min(end, total_pages)))
        else:
            indices.append(int(part) - 1)
    return sorted(set(indices))


def main():
    parser = argparse.ArgumentParser(
        description="Convert a PDF file to a Markdown document."
    )
    parser.add_argument("input", help="Input .pdf file")
    parser.add_argument("output", help="Output .md file")
    parser.add_argument(
        "--images",
        action="store_true",
        help="Extract images and save alongside the output file",
    )
    parser.add_argument(
        "--no-formatting",
        action="store_true",
        help="Disable heading detection from font sizes (plain text extraction)",
    )
    parser.add_argument(
        "--pages",
        default=None,
        help='Page range to convert, e.g. "1-5", "1,3,5", "2-" (default: all)',
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)
    if input_path.suffix.lower() != ".pdf":
        print(f"Warning: Input file does not look like a PDF: {input_path}")

    convert_pdf_to_markdown(
        input_path,
        output_path,
        extract_imgs=args.images,
        use_formatting=not args.no_formatting,
        pages=args.pages,
    )


if __name__ == "__main__":
    main()
