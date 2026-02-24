#!/usr/bin/env python3
"""
C Code Indexer — Wraps Universal Ctags to index C source files and generate indexing.md.

Universal Ctags does the actual parsing (no regex). This script:
  1. Runs ctags with JSON output + end-line fields
  2. Parses the JSON Lines output
  3. Generates a structured Markdown index
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from dataclasses import dataclass


@dataclass
class Symbol:
    name: str
    kind: str
    path: str         # absolute path
    start_line: int
    end_line: int     # may equal start_line if ctags doesn't compute end
    signature: str = ""
    typeref: str = ""
    scope: str = ""
    scope_kind: str = ""


# ── Kind display names ───────────────────────────────────────────────────────

KIND_DISPLAY = {
    "function":   "function",
    "struct":     "struct",
    "union":      "union",
    "enum":       "enum",
    "typedef":    "typedef",
    "macro":      "macro",
    "variable":   "variable",
    "enumerator": "enumerator",
    "member":     "member",
}

# Kinds to exclude by default from top-level index (can be overridden)
NOISE_KINDS = set()  # user can add "enumerator", "member" via --no-members


# ── Run ctags ────────────────────────────────────────────────────────────────

DEFAULT_EXCLUDES = [
    ".git", "build", "dist", "node_modules", "__pycache__", ".venv",
    "vendor", "target", "out", "bin", "obj", "CMakeFiles", ".cache",
]


def run_ctags(
    workspace: str,
    kinds: str = "dfegmstuv",
    exclude_dirs: list[str] = None,
    include_headers: bool = True,
    extra_args: list[str] = None,
) -> list[dict]:
    """Run Universal Ctags and return parsed JSON tag entries."""

    if exclude_dirs is None:
        exclude_dirs = DEFAULT_EXCLUDES

    cmd = [
        "ctags",
        "--languages=C",
        f"--kinds-C={kinds}",
        "--fields=+neKSZ",       # n=line, e=end, K=kind(long), S=signature, Z=scope
        "--fields-C=+{macrodef}",
        "--output-format=json",
        "--sort=no",             # we sort ourselves
        "-f", "-",               # stdout
        "--recurse",
    ]

    # File extension mapping
    if include_headers:
        cmd.append("--langmap=C:.c.h")
    else:
        cmd.append("--langmap=C:.c")

    # Exclude directories
    for d in exclude_dirs:
        cmd.append(f"--exclude={d}")

    # Extra user-provided args
    if extra_args:
        cmd.extend(extra_args)

    cmd.append(workspace)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        print("Error: 'ctags' not found. Install with: apt-get install -y universal-ctags",
              file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Error: ctags timed out (5 min limit)", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0 and result.stderr:
        print(f"ctags warnings: {result.stderr[:500]}", file=sys.stderr)

    tags = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        if obj.get("_type") != "tag":
            continue

        tags.append(obj)

    return tags


# ── Convert ctags JSON to Symbol list ────────────────────────────────────────

def tags_to_symbols(tags: list[dict], skip_kinds: set[str] = None) -> list[Symbol]:
    """Convert ctags JSON entries to Symbol dataclass instances."""
    if skip_kinds is None:
        skip_kinds = set()

    symbols = []
    for t in tags:
        kind = t.get("kind", "unknown")
        if kind in skip_kinds:
            continue

        name = t.get("name", "")
        if not name:
            continue
        # Skip compiler-generated anonymous names
        if name.startswith("__anon"):
            continue

        path = t.get("path", "")
        start_line = t.get("line", 0)
        end_line = t.get("end", start_line)  # fallback to start if no end

        signature = t.get("signature", "")
        typeref = t.get("typeref", "")
        scope = t.get("scope", "")
        scope_kind = t.get("scopeKind", "")

        symbols.append(Symbol(
            name=name,
            kind=kind,
            path=path,
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            typeref=typeref,
            scope=scope,
            scope_kind=scope_kind,
        ))

    return symbols


# ── Generate Markdown ────────────────────────────────────────────────────────

def format_detail(sym: Symbol) -> str:
    """Build a short detail string for the Detail column."""
    if sym.kind == "function":
        # Extract return type from typeref like "typename:int"
        if sym.typeref and ":" in sym.typeref:
            return sym.typeref.split(":", 1)[1]
    elif sym.kind == "typedef":
        if sym.typeref and ":" in sym.typeref:
            val = sym.typeref.split(":", 1)[1]
            if not val.startswith("__anon"):
                return val
    elif sym.kind in ("variable", "member"):
        if sym.typeref and ":" in sym.typeref:
            val = sym.typeref.split(":", 1)[1]
            if not val.startswith("__anon"):
                return val
    return ""


def generate_markdown(
    root: str,
    symbols: list[Symbol],
    sort_by: str = "file",
    show_stats: bool = True,
) -> str:
    """Generate indexing.md content from symbols."""
    lines = []
    lines.append("# Code Index\n")

    # Group by file
    by_file = defaultdict(list)
    for s in symbols:
        by_file[s.path].append(s)

    total_files = len(by_file)
    total_symbols = len(symbols)

    if show_stats:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines.append(f"> **Generated**: {now} | **Files**: {total_files} | **Symbols**: {total_symbols}")
        lines.append(f"> **Tool**: Universal Ctags (AST-based parser)\n")

        kind_counts = defaultdict(int)
        for s in symbols:
            kind_counts[s.kind] += 1
        if kind_counts:
            parts = [f"{k}: {v}" for k, v in sorted(kind_counts.items(), key=lambda x: -x[1])]
            lines.append(f"> {' | '.join(parts)}\n")

    lines.append("---\n")

    if sort_by == "name":
        all_syms = sorted(symbols, key=lambda s: s.name.lower())
        lines.append("| Name | Type | File | Lines | Detail |")
        lines.append("|------|------|------|-------|--------|")
        for s in all_syms:
            rel = os.path.relpath(s.path, root)
            detail = format_detail(s)
            lines.append(
                f"| `{s.name}` | {s.kind} | `{rel}` | {s.start_line} - {s.end_line} | {detail} |"
            )

    elif sort_by == "type":
        by_kind = defaultdict(list)
        for s in symbols:
            by_kind[s.kind].append(s)

        kind_order = ["function", "struct", "union", "enum", "typedef",
                       "macro", "variable", "enumerator", "member"]
        for kind in kind_order:
            if kind not in by_kind:
                continue
            entries = sorted(by_kind[kind], key=lambda s: s.name.lower())
            label = kind.capitalize() + ("es" if kind.endswith("s") else "s")
            lines.append(f"\n## {label} ({len(entries)})\n")
            lines.append("| Name | File | Lines | Detail |")
            lines.append("|------|------|-------|--------|")
            for s in entries:
                rel = os.path.relpath(s.path, root)
                detail = format_detail(s)
                lines.append(
                    f"| `{s.name}` | `{rel}` | {s.start_line} - {s.end_line} | {detail} |"
                )

    else:  # sort by file (default)
        sorted_files = sorted(by_file.keys())
        for filepath in sorted_files:
            file_syms = sorted(by_file[filepath], key=lambda s: s.start_line)
            rel = os.path.relpath(filepath, root)
            lines.append(f"\n## `{rel}`\n")
            lines.append("| Type | Name | Lines | Detail |")
            lines.append("|------|------|-------|--------|")
            for s in file_syms:
                detail = format_detail(s)
                scope_info = f" ({s.scope_kind} `{s.scope}`)" if s.scope else ""
                lines.append(
                    f"| {s.kind} | `{s.name}`{scope_info} | {s.start_line} - {s.end_line} | {detail} |"
                )

    lines.append("")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Index C source code using Universal Ctags → indexing.md"
    )
    parser.add_argument("workspace", help="Root directory to scan")
    parser.add_argument("-o", "--output", help="Output file (default: <workspace>/indexing.md)")
    parser.add_argument("--exclude", help="Comma-separated dir names to exclude", default=None)
    parser.add_argument("--no-headers", action="store_true", help="Skip .h files")
    parser.add_argument("--kinds", default="dfegmstuv",
                        help="ctags C kind letters to include (default: dfegmstuv = all)")
    parser.add_argument("--sort", choices=["file", "name", "type"], default="file")
    parser.add_argument("--no-stats", action="store_true")
    parser.add_argument("--no-members", action="store_true",
                        help="Exclude struct/union members and enumerators")
    parser.add_argument("--ctags-args", default="",
                        help="Extra arguments passed directly to ctags")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    workspace = os.path.abspath(args.workspace)
    if not os.path.isdir(workspace):
        print(f"Error: '{workspace}' is not a directory", file=sys.stderr)
        sys.exit(1)

    output = args.output or os.path.join(workspace, "indexing.md")

    exclude_dirs = DEFAULT_EXCLUDES[:]
    if args.exclude:
        exclude_dirs = [d.strip() for d in args.exclude.split(",")]

    extra_args = []
    if args.ctags_args:
        extra_args = args.ctags_args.split()

    if args.verbose:
        print(f"Scanning {workspace}...", file=sys.stderr)

    # Run ctags
    tags = run_ctags(
        workspace,
        kinds=args.kinds,
        exclude_dirs=exclude_dirs,
        include_headers=not args.no_headers,
        extra_args=extra_args,
    )

    if args.verbose:
        print(f"ctags returned {len(tags)} tags", file=sys.stderr)

    # Convert to symbols
    skip_kinds = set()
    if args.no_members:
        skip_kinds = {"member", "enumerator"}

    symbols = tags_to_symbols(tags, skip_kinds=skip_kinds)

    if args.verbose:
        print(f"Processing {len(symbols)} symbols...", file=sys.stderr)

    # Generate markdown
    md = generate_markdown(workspace, symbols, args.sort, not args.no_stats)

    # Write
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Indexed {len(symbols)} symbols in {len(set(s.path for s in symbols))} files → {output}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
