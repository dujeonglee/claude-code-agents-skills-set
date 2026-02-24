#!/usr/bin/env python3
"""
C Code Indexer Search Utility
Searches the generated indexing.md file for symbols by name and/or type.
Automatically re-indexes if source files are newer than indexing.md.

Usage:
    # Search from existing indexing.md (auto-reindex if stale)
    python3 search_index.py <workspace> <name> [--type TYPE] [--exact-match]

    # Or specify indexing.md directly
    python3 search_index.py <indexing.md> <name> [--type TYPE] [--exact-match]

    # Force re-index
    python3 search_index.py <workspace> <name> --force-index

    # Only re-index
    python3 search_index.py <workspace> --index-only [--ctags-path PATH]
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from collections import defaultdict


# ── Memory Functions ───────────────────────────────────────────────────────────


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


def clear_failure(workspace: str) -> None:
    """Clear the last failure from memory."""
    memory = read_memory(workspace)
    if "last_failure" in memory:
        del memory["last_failure"]
        write_memory(workspace, memory)


def record_search_success(workspace: str) -> None:
    """Record a successful search operation in memory."""
    memory = read_memory(workspace)
    memory["last_search"] = {
        "timestamp": datetime.now().isoformat(),
    }
    write_memory(workspace, memory)


def record_search_failure(workspace: str, error: str, name: str, type_filter: str | None = None) -> None:
    """Record a failed search operation in memory."""
    memory = read_memory(workspace)
    memory["last_search_failure"] = {
        "timestamp": datetime.now().isoformat(),
        "error": error,
        "name": name,
        "type": type_filter,
    }
    write_memory(workspace, memory)


def print_search_memory_advice(workspace: str) -> str | None:
    """Print advice based on memory of past search failures."""
    memory = read_memory(workspace)
    last_failure = memory.get("last_search_failure", {})

    if last_failure:
        error = last_failure.get("error", "")
        name = last_failure.get("name", "")
        if "no matching symbols found" in error.lower():
            suggested = memory.get("suggested_ctags_path")
            if suggested and os.path.isfile(suggested):
                print(f"> **Note**: Previous search for '{name}' found nothing. "
                      f"Re-indexing with ctags: {suggested}", file=sys.stderr)
                return suggested

    return None


def run_ctags(ctags_path: str, workspace: str, kinds: str = "dfegmstuv",
              extra_args: list[str] = None) -> list[dict]:
    """Run Universal Ctags and return parsed JSON tag entries."""
    cmd = [
        ctags_path,
        "--languages=C",
        f"--kinds-C={kinds}",
        "--fields=+neKSZ",
        "--fields-C=+{macrodef}",
        "--output-format=json",
        "--sort=no",
        "-f", "-",
        "--recurse",
        "--langmap=C:.c.h",
    ]

    if extra_args:
        cmd.extend(extra_args)

    cmd.append(workspace)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        print(f"Error: '{ctags_path}' not found. Install Universal Ctags.",
              file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Error: ctags timed out (5 min limit)", file=sys.stderr)
        sys.exit(1)

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


def tags_to_symbols(tags: list[dict]) -> list[dict]:
    """Convert ctags JSON entries to simplified symbol dicts."""
    symbols = []
    for t in tags:
        kind = t.get("kind", "unknown")
        name = t.get("name", "")
        if not name or name.startswith("__anon"):
            continue

        path = t.get("path", "")
        start_line = t.get("line", 0)
        end_line = t.get("end", start_line)

        typeref = t.get("typeref", "")
        detail = ""
        if typeref and ":" in typeref:
            detail = typeref.split(":", 1)[1]

        symbols.append({
            "name": name,
            "kind": kind,
            "path": os.path.abspath(path),
            "start_line": start_line,
            "end_line": end_line,
            "detail": detail,
        })

    return symbols


def parse_indexing_md(filepath: str) -> list[dict]:
    """
    Parse indexing.md file with consistent format:
    | Name | File | Type | Lines | Detail |
    """
    symbols = []

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    in_table = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Detect table header
        if line.startswith("| Name |"):
            in_table = True
            continue

        if not in_table:
            continue

        # End of table
        if line.startswith("|---"):
            continue

        # Parse row: | Name | File | Type | Lines | Detail |
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue

        name = parts[1].strip("`").strip()
        file_path = parts[2].strip("`").strip()
        sym_type = parts[3].strip()
        line_range = parts[4]

        # Parse line range (e.g., "42 - 56")
        if " - " in line_range:
            start_line, end_line = line_range.split(" - ")
            try:
                start_line = int(start_line.strip())
                end_line = int(end_line.strip())
            except ValueError:
                continue
        else:
            try:
                start_line = int(line_range.strip())
                end_line = start_line
            except ValueError:
                continue

        symbols.append({
            "name": name,
            "file": file_path,
            "type": sym_type,
            "kind": sym_type,
            "line_range": f"{start_line}-{end_line}",
            "start_line": start_line,
            "end_line": end_line,
        })

    return symbols


def search_symbols(symbols: list[dict], name: str,
                   type_filter: str = None, exact_match: bool = False) -> list[dict]:
    """Search symbols by name and optionally type."""
    results = []

    for sym in symbols:
        if exact_match:
            if sym["name"] != name:
                continue
        else:
            if name.lower() not in sym["name"].lower():
                continue

        if type_filter:
            sym_type = sym.get("kind", "") or sym.get("type", "")
            if type_filter.lower() not in sym_type.lower():
                continue

        results.append(sym)

    return results


def format_results(results: list[dict], show_all: bool = False) -> str:
    """Format search results for display."""
    if not results:
        return "No matching symbols found."

    lines = [f"Found {len(results)} matching symbol(s):\n"]

    by_file = defaultdict(list)
    for r in results:
        by_file[r["file"]].append(r)

    for filepath in sorted(by_file.keys()):
        lines.append(f"\n📄 {filepath}")
        entries = by_file[filepath]

        by_type = defaultdict(list)
        for e in entries:
            kind = e.get("kind") or e.get("type") or "unknown"
            by_type[kind].append(e)

        if show_all:
            for sym_type, type_entries in sorted(by_type.items()):
                for e in type_entries:
                    lines.append(f"   • `{e['name']}` ({sym_type}) - Lines {e['start_line']}-{e['end_line']}")
        else:
            for sym_type, type_entries in sorted(by_type.items()):
                # Show first match per type
                e = type_entries[0]
                lines.append(f"   • `{e['name']}` ({sym_type}) - Lines {e['start_line']}-{e['end_line']}")

    return "\n".join(lines)


def get_ctags_kind_for_type(type_name: str) -> str:
    """Map a type name to ctags kind letter."""
    type_map = {
        "function": "f",
        "struct": "s",
        "union": "u",
        "enum": "g",
        "typedef": "t",
        "macro": "d",
        "variable": "v",
        "enumerator": "e",
        "member": "m",
    }
    return type_map.get(type_name.lower(), "")


def needs_reindex(workspace: str, indexing_path: str) -> bool:
    """Check if workspace files are newer than indexing.md."""
    if not os.path.isfile(indexing_path):
        return True

    indexing_mtime = os.path.getmtime(indexing_path)

    for root, dirs, files in os.walk(workspace):
        # Skip common build directories
        dirs[:] = [d for d in dirs if d not in {'.git', 'build', 'node_modules', '__pycache__'}]
        for f in files:
            if f.endswith(('.c', '.h')):
                file_mtime = os.path.getmtime(os.path.join(root, f))
                if file_mtime > indexing_mtime:
                    return True

    return False


def generate_indexing_md(ctags_symbols: list[dict], workspace: str) -> str:
    """Generate indexing.md content from ctags symbols."""
    lines = ["# Code Index\n"]

    by_kind = defaultdict(list)
    for s in ctags_symbols:
        by_kind[s["kind"]].append(s)

    total_files = len(set(s["path"] for s in ctags_symbols))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"> **Generated**: {now} | **Files**: {total_files} | **Symbols**: {len(ctags_symbols)}\n")

    kind_counts = defaultdict(int)
    for s in ctags_symbols:
        kind_counts[s["kind"]] += 1
    if kind_counts:
        parts = [f"{k}: {v}" for k, v in sorted(kind_counts.items(), key=lambda x: -x[1])]
        lines.append(f"> {' | '.join(parts)}\n")

    lines.append("---\n")
    lines.append("| Name | File | Type | Lines | Detail |")
    lines.append("|------|------|------|-------|--------|")

    for s in sorted(ctags_symbols, key=lambda x: x["name"].lower()):
        rel_path = os.path.relpath(s["path"], workspace)
        lines.append(f"| `{s['name']}` | `{rel_path}` | {s['kind']} | {s['start_line']}-{s['end_line']} | {s['detail']} |")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Search C symbols by name and type with auto-reindexing"
    )
    parser.add_argument("target", help="Path to indexing.md OR workspace directory")
    parser.add_argument("name", nargs="?", help="Symbol name to search for (required unless --index-only)")
    parser.add_argument("--type", help="Filter by symbol type (comma-separated)")
    parser.add_argument("--exact-match", action="store_true", help="Exact name match only")
    parser.add_argument("--show-all", action="store_true", help="Show all matches, not just first per type")
    parser.add_argument("--ctags-path", default="ctags", help="Full path to ctags executable")
    parser.add_argument("--force-index", action="store_true", help="Force re-indexing")
    parser.add_argument("--index-only", action="store_true", help="Only re-index, don't search")
    parser.add_argument("--indexing", help="Path to indexing.md (when target is workspace)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Check if target is indexing.md or workspace
    target_is_indexing = args.name is None or os.path.isfile(args.target)

    # Determine workspace and indexing paths
    if os.path.isfile(args.target):
        indexing_path = os.path.abspath(args.target)
        workspace = os.path.dirname(indexing_path)
        # If name was provided as first arg but target is file, use name as search term
        search_name = args.name or ""
    else:
        workspace = os.path.abspath(args.target)
        if args.indexing:
            indexing_path = os.path.abspath(args.indexing)
        else:
            indexing_path = os.path.join(workspace, "indexing.md")
        search_name = args.name or ""

    # Handle --index-only mode
    if args.index_only:
        if args.verbose:
            print(f"Indexing workspace: {workspace}", file=sys.stderr)
        tags = run_ctags(
            args.ctags_path,
            workspace,
            kinds="dfegmstuv",
            extra_args=["--exclude=.git", "--exclude=build", "--exclude=node_modules"],
        )
        ctags_symbols = tags_to_symbols(tags)
        md_content = generate_indexing_md(ctags_symbols, workspace)

        os.makedirs(os.path.dirname(indexing_path), exist_ok=True)
        with open(indexing_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        total_files = len(set(s["path"] for s in ctags_symbols))
        print(f"Indexed {len(ctags_symbols)} symbols in {total_files} files → {indexing_path}",
              file=sys.stderr)
        return

    # Validate search name
    if not search_name:
        print("Error: Symbol name required for search", file=sys.stderr)
        sys.exit(1)

    # Determine if re-index is needed
    needs_index = args.force_index or needs_reindex(workspace, indexing_path)

    # Build kind filter based on --type
    kinds = "dfegmstuv"
    if args.type:
        type_kinds = set()
        for t in args.type.split(","):
            kind_letter = get_ctags_kind_for_type(t.strip())
            if kind_letter:
                type_kinds.add(kind_letter)
        if type_kinds:
            kinds = "".join(sorted(type_kinds))
            if args.verbose:
                print(f"Using kind filter: {kinds}", file=sys.stderr)

    # Re-index if needed
    ctags_symbols = None
    if needs_index:
        if args.verbose:
            if args.force_index:
                print("Forcing re-indexing...", file=sys.stderr)
            else:
                print(f"Source files are newer than indexing.md, re-indexing...", file=sys.stderr)

        tags = run_ctags(
            args.ctags_path,
            workspace,
            kinds=kinds,
            extra_args=["--exclude=.git", "--exclude=build", "--exclude=node_modules"],
        )
        ctags_symbols = tags_to_symbols(tags)

        # Update indexing.md
        md_content = generate_indexing_md(ctags_symbols, workspace)
        os.makedirs(os.path.dirname(indexing_path), exist_ok=True)
        with open(indexing_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        if args.verbose:
            print(f"Updated indexing.md with {len(ctags_symbols)} symbols", file=sys.stderr)

        # Search directly from ctags symbols
        results = search_symbols(ctags_symbols, search_name, args.type, args.exact_match)
        print(format_results(results, args.show_all))
        return

    # Search from existing indexing.md
    if args.verbose:
        print(f"Reading indexing from: {indexing_path}", file=sys.stderr)

    try:
        symbols = parse_indexing_md(indexing_path)
    except FileNotFoundError:
        print(f"Error: File not found: {indexing_path}", file=sys.stderr)
        print("Run with --index-only or --force-index to create indexing.md", file=sys.stderr)
        record_search_failure(workspace, "File not found", search_name, args.type)
        sys.exit(1)

    if args.verbose:
        print(f"Parsed {len(symbols)} symbols from {indexing_path}", file=sys.stderr)

    # Search
    results = search_symbols(symbols, search_name, args.type, args.exact_match)

    # Display results
    output = format_results(results, args.show_all)
    print(output)

    # Record success or failure
    if not results:
        record_search_failure(workspace, "No matching symbols found", search_name, args.type)
    else:
        record_search_success(workspace)


if __name__ == "__main__":
    main()
