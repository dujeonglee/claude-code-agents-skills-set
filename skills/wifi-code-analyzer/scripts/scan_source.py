#!/usr/bin/env python3
"""scan_source.py — Deterministic source-tree scanner for WiFi driver analysis.

Reads a Linux WiFi driver source directory and produces a JSON report with:
  - File inventory (*.c, *.h, Makefile) with LOC per file
  - #include edges (local vs. kernel)
  - Function-name prefix frequency
  - Makefile obj-y / obj-$(CONFIG_*) targets
  - Struct definitions per header
  - Total LOC and inferred driver name

Does NOT perform: module detection, cohesion scoring, dependency analysis
beyond raw include edges.

Usage:
    python3 scan_source.py <driver_source_path> [--output <path>]

Output defaults to stdout as JSON.  With --output, writes to the given file.

Requirements: Python 3.8+, no external dependencies.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------
RE_INCLUDE_LOCAL = re.compile(r'^\s*#include\s+"([^"]+)"', re.MULTILINE)
RE_INCLUDE_KERNEL = re.compile(r'^\s*#include\s+<([^>]+)>', re.MULTILINE)
RE_FUNC_DEF = re.compile(
    r'^(?!static\s+inline)'           # skip static inline (header helpers)
    r'(?:static\s+)?'                 # optional static
    r'(?:(?:void|int|bool|unsigned|long|u8|u16|u32|u64|s8|s16|s32|s64|'
    r'size_t|ssize_t|enum\s+\w+|struct\s+\w+\s*\*?)\s+)'  # return type
    r'(\w+)\s*\(',                     # function name
    re.MULTILINE,
)
RE_STRUCT_DEF = re.compile(
    r'^\s*(?:typedef\s+)?struct\s+(\w+)\s*\{', re.MULTILINE
)
RE_OBJ_TARGET = re.compile(
    r'^\s*([\w-]+)-(?:y|objs|\$\(CONFIG_\w+\))\s*[\+:]?=\s*(.*)',
    re.MULTILINE,
)
# Names that are Kbuild directives, not build-unit targets
_KBUILD_DIRECTIVES = frozenset({
    "ccflags", "asflags", "ldflags", "subdir-ccflags",
    "clean-files", "always", "hostprogs", "extra",
    "targets", "subdir",
})
RE_OBJ_FILE = re.compile(r'([\w-]+)\.o')


def count_lines(path: Path) -> int:
    """Count non-empty lines in a file."""
    try:
        text = path.read_text(errors="replace")
        return sum(1 for line in text.splitlines() if line.strip())
    except OSError:
        return 0


def extract_includes(text: str):
    """Return (local_includes, kernel_includes) lists."""
    local = RE_INCLUDE_LOCAL.findall(text)
    kernel = RE_INCLUDE_KERNEL.findall(text)
    return local, kernel


def extract_function_names(text: str):
    """Return list of function names defined in C source."""
    return RE_FUNC_DEF.findall(text)


def extract_struct_defs(text: str):
    """Return list of struct names defined in a header."""
    return RE_STRUCT_DEF.findall(text)


def compute_prefix_freq(func_names: list[str]) -> dict[str, int]:
    """Compute function-name prefix frequencies.

    A prefix is everything up to and including the last underscore before the
    "verb" part.  For example, ath10k_wmi_cmd_send -> prefix "ath10k_wmi_".
    We try progressively shorter underscore-delimited prefixes.
    """
    prefix_counter: Counter = Counter()
    for name in func_names:
        parts = name.split("_")
        # Try prefixes of length 2 and 3 (e.g., "ath10k_wmi_", "scsc_wifi_")
        for depth in (3, 2):
            if len(parts) > depth:
                prefix = "_".join(parts[:depth]) + "_"
                prefix_counter[prefix] += 1

    # Filter: only keep prefixes that appear 3+ times
    return {k: v for k, v in prefix_counter.most_common() if v >= 3}


def parse_makefile(makefile_path: Path) -> list[dict]:
    """Parse Makefile for obj-y / obj-$(CONFIG_*) targets.

    Merges multiple += lines for the same target into one entry.
    """
    merged: dict[str, list[str]] = {}
    try:
        text = makefile_path.read_text(errors="replace")
    except OSError:
        return []

    for match in RE_OBJ_TARGET.finditer(text):
        target_name = match.group(1)
        if target_name in _KBUILD_DIRECTIVES:
            continue
        obj_line = match.group(2).strip()

        # Handle multi-line continuations
        pos = match.end()
        while obj_line.endswith("\\"):
            obj_line = obj_line[:-1].strip()
            nl = text.find("\n", pos)
            if nl == -1:
                break
            next_line_end = text.find("\n", nl + 1)
            if next_line_end == -1:
                next_line_end = len(text)
            obj_line += " " + text[nl + 1 : next_line_end].strip()
            pos = next_line_end

        obj_files = [f + ".c" for f in RE_OBJ_FILE.findall(obj_line)]
        if target_name not in merged:
            merged[target_name] = []
        merged[target_name].extend(obj_files)

    # Deduplicate objects within each target while preserving order
    targets = []
    for name, objs in merged.items():
        seen: set[str] = set()
        unique = []
        for o in objs:
            if o not in seen:
                seen.add(o)
                unique.append(o)
        targets.append({"target": name, "objects": unique})
    return targets


def infer_driver_name(source_path: Path) -> str:
    """Infer driver name from directory name or Makefile target."""
    return source_path.name


def scan_directory(source_path: Path) -> dict:
    """Scan the driver source directory and produce the full report."""
    source_path = source_path.resolve()
    if not source_path.is_dir():
        print(f"Error: {source_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    driver_name = infer_driver_name(source_path)

    # Collect source files (non-recursive — driver sources are typically flat)
    c_files = sorted(source_path.glob("*.c"))
    h_files = sorted(source_path.glob("*.h"))
    makefiles = list(source_path.glob("Makefile")) + list(
        source_path.glob("Kbuild")
    )

    # Also check one level of subdirectories for drivers that use them
    for subdir in sorted(source_path.iterdir()):
        if subdir.is_dir() and not subdir.name.startswith("."):
            c_files.extend(sorted(subdir.glob("*.c")))
            h_files.extend(sorted(subdir.glob("*.h")))
            makefiles.extend(list(subdir.glob("Makefile")))
            makefiles.extend(list(subdir.glob("Kbuild")))

    all_files = c_files + h_files
    total_loc = 0
    file_inventory = []
    include_edges = []
    all_func_names = []
    struct_defs_by_file = {}

    for fpath in all_files:
        try:
            text = fpath.read_text(errors="replace")
        except OSError:
            continue

        loc = sum(1 for line in text.splitlines() if line.strip())
        total_loc += loc
        rel = str(fpath.relative_to(source_path))

        file_entry = {
            "file": rel,
            "type": "source" if fpath.suffix == ".c" else "header",
            "loc": loc,
        }
        file_inventory.append(file_entry)

        # Includes
        local_inc, kernel_inc = extract_includes(text)
        for inc in local_inc:
            include_edges.append(
                {"from": rel, "to": inc, "kind": "local"}
            )
        for inc in kernel_inc:
            include_edges.append(
                {"from": rel, "to": inc, "kind": "kernel"}
            )

        # Functions (only from .c files)
        if fpath.suffix == ".c":
            funcs = extract_function_names(text)
            all_func_names.extend(funcs)
            file_entry["functions"] = funcs

        # Structs (only from .h files)
        if fpath.suffix == ".h":
            structs = extract_struct_defs(text)
            if structs:
                struct_defs_by_file[rel] = structs

    # Prefix frequency
    prefix_freq = compute_prefix_freq(all_func_names)

    # Makefile targets
    makefile_targets = []
    for mf in makefiles:
        targets = parse_makefile(mf)
        if targets:
            rel_mf = str(mf.relative_to(source_path))
            for t in targets:
                t["makefile"] = rel_mf
            makefile_targets.extend(targets)

    report = {
        "driver_name": driver_name,
        "source_path": str(source_path),
        "total_files": len(all_files),
        "total_c_files": len(c_files),
        "total_h_files": len(h_files),
        "total_loc": total_loc,
        "file_inventory": file_inventory,
        "include_edges": include_edges,
        "prefix_frequency": prefix_freq,
        "makefile_targets": makefile_targets,
        "struct_definitions": struct_defs_by_file,
    }

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Scan WiFi driver source tree and produce JSON report."
    )
    parser.add_argument(
        "source_path",
        type=Path,
        help="Path to the driver source directory",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args()

    report = scan_directory(args.source_path)
    json_str = json.dumps(report, indent=2, ensure_ascii=False)

    if args.output:
        args.output.write_text(json_str + "\n")
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(json_str)


if __name__ == "__main__":
    main()
