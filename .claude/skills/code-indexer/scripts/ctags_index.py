#!/usr/bin/env python3
"""
C Code Indexer — Wraps Universal Ctags to index C source files and generate indexing.md.

Universal Ctags does the actual parsing (no regex). This script:
  1. Runs ctags with JSON output + end-line fields
  2. Parses the JSON Lines output
  3. Generates a structured Markdown index
  4. Uses memory to remember past failures and avoid repeating them
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
    ctags_path: str,
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
        ctags_path,
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
        print(f"Error: '{ctags_path}' not found. Install Universal Ctags or specify a different path.",
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
    show_stats: bool = True,
) -> str:
    """Generate indexing.md content from symbols.

    Consistent format (name-sorted):
    | Name | File | Type | Lines | Detail |
    |------|------|------|-------|--------|
    """
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
    lines.append("| Name | File | Type | Lines | Detail |")
    lines.append("|------|------|------|-------|--------|")

    # Sort by name for consistent output
    for s in sorted(symbols, key=lambda x: x.name.lower()):
        rel = os.path.relpath(s.path, root)
        detail = format_detail(s)
        lines.append(
            f"| `{s.name}` | `{rel}` | {s.kind} | {s.start_line} - {s.end_line} | {detail} |"
        )

    lines.append("")
    return "\n".join(lines)


def write_memory(workspace: str, memory: dict) -> None:
    """Write memory to the skill cache directory."""
    cache_dir = os.path.join(workspace, ".claude", "skill-cache")
    os.makedirs(cache_dir, exist_ok=True)
    memory_path = os.path.join(cache_dir, "code-indexer.json")
    with open(memory_path, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2)


def read_memory(workspace: str) -> dict:
    """Read memory from the skill cache directory."""
    memory_path = os.path.join(workspace, ".claude", "skill-cache", "code-indexer.json")
    if os.path.isfile(memory_path):
        try:
            with open(memory_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def get_suggested_ctags_path(workspace: str) -> str | None:
    """Get a suggested ctags path from memory, if available."""
    memory = read_memory(workspace)
    return memory.get("suggested_ctags_path")


def set_suggested_ctags_path(workspace: str, ctags_path: str) -> None:
    """Set a suggested ctags path in memory."""
    memory = read_memory(workspace)
    memory["suggested_ctags_path"] = ctags_path
    memory["last_failure"] = {}
    write_memory(workspace, memory)


def clear_failure(workspace: str) -> None:
    """Clear the last failure from memory."""
    memory = read_memory(workspace)
    memory.pop("last_failure", None)
    memory.pop("last_success", None)
    memory.pop("suggested_ctags_path", None)
    write_memory(workspace, memory)


def record_success(workspace: str, files_indexed: int, symbols_found: int) -> None:
    """Record a successful indexing operation in memory."""
    memory = read_memory(workspace)
    memory["last_success"] = {
        "timestamp": datetime.now().isoformat(),
        "files_indexed": files_indexed,
        "symbols_found": symbols_found,
    }
    if "last_failure" in memory:
        del memory["last_failure"]
    write_memory(workspace, memory)


def record_failure(workspace: str, error: str, command: str) -> None:
    """Record a failed indexing operation in memory."""
    memory = read_memory(workspace)
    memory["last_failure"] = {
        "timestamp": datetime.now().isoformat(),
        "error": error,
        "command": command,
        "workspace": workspace,
    }

    # Suggest known ctags paths based on error
    if "ctags" in error.lower() and "not found" in error.lower():
        for path in ["/opt/homebrew/bin/ctags", "/usr/local/bin/ctags", "ctags"]:
            if os.path.isfile(path):
                memory["suggested_ctags_path"] = path
                break

    write_memory(workspace, memory)


def print_memory_advice(workspace: str) -> None:
    """Print advice based on memory of past failures."""
    memory = read_memory(workspace)
    last_failure = memory.get("last_failure", {})

    if last_failure:
        error = last_failure.get("error", "")
        if "ctags not found" in error.lower() or "not found" in error.lower():
            suggested = memory.get("suggested_ctags_path")
            if suggested and os.path.isfile(suggested):
                print(f"> **Note**: Previous ctags failure detected. Using: {suggested}",
                      file=sys.stderr)
                return suggested

    return None


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
    parser.add_argument("--no-stats", action="store_true")
    parser.add_argument("--no-members", action="store_true",
                        help="Exclude struct/union members and enumerators")
    parser.add_argument("--ctags-args", default="",
                        help="Extra arguments passed directly to ctags")
    parser.add_argument("--ctags-path", default="ctags",
                        help="Full path to ctags executable (default: 'ctags' in PATH)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--clear-memory", action="store_true",
                        help="Clear memory of past failures")

    args = parser.parse_args()

    workspace = os.path.abspath(args.workspace)
    if not os.path.isdir(workspace):
        print(f"Error: '{workspace}' is not a directory", file=sys.stderr)
        sys.exit(1)

    output = args.output or os.path.join(workspace, "indexing.md")

    # Handle memory clearing
    if args.clear_memory:
        clear_failure(workspace)
        print("Memory cleared", file=sys.stderr)
        sys.exit(0)

    # Check memory for past failures
    suggested_path = print_memory_advice(workspace)

    # If previous failure was ctags not found, try suggested path
    if suggested_path and args.ctags_path == "ctags":
        args.ctags_path = suggested_path
        if args.verbose:
            print(f"Using ctags from memory: {args.ctags_path}", file=sys.stderr)

    exclude_dirs = DEFAULT_EXCLUDES[:]
    if args.exclude:
        exclude_dirs = [d.strip() for d in args.exclude.split(",")]

    extra_args = []
    if args.ctags_args:
        extra_args = args.ctags_args.split()

    if args.verbose:
        print(f"Scanning {workspace}...", file=sys.stderr)
        print(f"Using ctags: {args.ctags_path}", file=sys.stderr)

    try:
        # Run ctags
        tags = run_ctags(
            args.ctags_path,
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
        md = generate_markdown(workspace, symbols, not args.no_stats)

        # Write
        os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            f.write(md)

        file_count = len(set(s.path for s in symbols))
        print(f"Indexed {len(symbols)} symbols in {file_count} files → {output}",
              file=sys.stderr)

        # Record success
        record_success(workspace, file_count, len(symbols))

    except Exception as e:
        # Record failure
        cmd_str = f"{args.ctags_path} --languages=C --kinds-C={args.kinds} ..."
        record_failure(workspace, str(e), cmd_str)
        raise


if __name__ == "__main__":
    main()
