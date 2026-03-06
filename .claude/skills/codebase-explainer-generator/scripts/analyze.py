#!/usr/bin/env python3
"""Codebase analyzer for the codebase-explainer-generator skill.

Gathers structured data about a codebase that Claude uses to write
architecture documentation. Pure Python 3.8+, no external packages.

Usage:
    python3 analyze.py <workspace> [options]
    python3 analyze.py /path/to/project --format json --output analysis.json
    python3 analyze.py /path/to/project --format text
    python3 analyze.py diff <old_analysis.json> <new_analysis.json> [--output changes.json]
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_KEY_FILES = 30
MAX_ENTRY_POINTS = 30

SOURCE_EXTENSIONS: Dict[str, str] = {
    # C / C++
    ".c": "c", ".h": "c", ".cc": "c++", ".cpp": "c++", ".cxx": "c++",
    ".hh": "c++", ".hpp": "c++", ".hxx": "c++",
    # Python
    ".py": "python", ".pyi": "python",
    # Java / Kotlin
    ".java": "java", ".kt": "java",
    # Go
    ".go": "go",
    # Rust
    ".rs": "rust",
    # JavaScript / TypeScript
    ".js": "js", ".jsx": "js", ".mjs": "js",
    ".ts": "ts", ".tsx": "ts",
    # Ruby
    ".rb": "ruby",
    # Swift
    ".swift": "swift",
    # Shell
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
}

EXCLUDE_DIRS: Set[str] = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".tox", ".eggs", "*.egg-info", "build", "dist",
    ".doxygen", ".cache", ".venv", "venv", "env", ".env",
    ".idea", ".vscode", ".eclipse", "target", "out", "bin",
    ".claude",
}

BUILD_FILES: Dict[str, str] = {
    "Makefile": "make", "makefile": "make", "GNUmakefile": "make",
    "CMakeLists.txt": "cmake",
    "Cargo.toml": "cargo",
    "package.json": "npm",
    "go.mod": "go",
    "pyproject.toml": "pyproject",
    "setup.py": "setuptools", "setup.cfg": "setuptools",
    "BUILD": "bazel", "BUILD.bazel": "bazel", "WORKSPACE": "bazel",
    "meson.build": "meson",
    "SConstruct": "scons",
    "Kconfig": "kbuild", "Kbuild": "kbuild",
    "build.gradle": "gradle", "build.gradle.kts": "gradle",
    "pom.xml": "maven",
    "Gemfile": "bundler",
    "Rakefile": "rake",
    "mix.exs": "mix",
    "dune-project": "dune",
    "cabal.project": "cabal",
    "stack.yaml": "stack",
}

CONFIG_PATTERNS: List[str] = [
    "Kconfig", ".config", "*.toml", "*.yaml", "*.yml",
    "*.ini", "*.cfg", "*.conf", ".env.example",
    "tsconfig.json", "jest.config.*", "webpack.config.*",
    ".eslintrc*", ".prettierrc*", "tox.ini", "pytest.ini",
]

ENTRY_POINT_PATTERNS: Dict[str, List[re.Pattern]] = {
    "c": [
        re.compile(r"^\s*(?:int|void)\s+main\s*\(", re.MULTILINE),
        re.compile(r"(?<!\w)module_init\s*\(\s*(\w+)\s*\)", re.MULTILINE),
        re.compile(r"(?<!\w)module_exit\s*\(\s*(\w+)\s*\)", re.MULTILINE),
        re.compile(r"(?<!\w)late_initcall\s*\(\s*(\w+)\s*\)", re.MULTILINE),
        re.compile(r"(?<!\w)subsys_initcall\s*\(\s*(\w+)\s*\)", re.MULTILINE),
    ],
    "c++": [
        re.compile(r"^\s*int\s+main\s*\(", re.MULTILINE),
    ],
    "python": [
        re.compile(r"""if\s+__name__\s*==\s*['"]__main__['"]\s*:""", re.MULTILINE),
    ],
    "java": [
        re.compile(r"public\s+static\s+void\s+main\s*\(", re.MULTILINE),
    ],
    "go": [
        re.compile(r"^func\s+main\s*\(\s*\)", re.MULTILINE),
    ],
    "rust": [
        re.compile(r"^fn\s+main\s*\(\s*\)", re.MULTILINE),
    ],
    "js": [
        re.compile(r"""['"]main['"]\s*:\s*['"]"""),  # package.json main field
    ],
    "ts": [
        re.compile(r"""['"]main['"]\s*:\s*['"]"""),
    ],
}

INDEX_FILES: Dict[str, List[str]] = {
    "python": ["__init__.py", "__main__.py"],
    "js": ["index.js", "index.jsx", "index.mjs"],
    "ts": ["index.ts", "index.tsx"],
    "rust": ["main.rs", "lib.rs", "mod.rs"],
    "go": ["main.go"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def count_lines(path: Path) -> int:
    """Count lines in a file, handling encoding errors."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except (OSError, IOError):
        return 0


def read_head(path: Path, max_bytes: int = 32768) -> str:
    """Read the first max_bytes of a file as text."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(max_bytes)
    except (OSError, IOError):
        return ""


def is_excluded(path: Path, extra_excludes: Set[str]) -> bool:
    """Check if any component of path matches exclusion patterns."""
    all_excludes = EXCLUDE_DIRS | extra_excludes
    for part in path.parts:
        if part in all_excludes:
            return True
        # Glob-style matching for patterns like *.egg-info
        for pattern in all_excludes:
            if "*" in pattern:
                import fnmatch
                if fnmatch.fnmatch(part, pattern):
                    return True
    return False


def relative_dir(filepath: Path, workspace: Path) -> str:
    """Get the relative directory of a file from the workspace root."""
    try:
        rel = filepath.parent.relative_to(workspace)
        return str(rel) + "/" if str(rel) != "." else "./"
    except ValueError:
        return "./"


# ---------------------------------------------------------------------------
# Language Detection
# ---------------------------------------------------------------------------

def detect_language(workspace: Path, file_ext_counts: Counter) -> str:
    """Detect the primary language of the project."""
    # Map extensions to language votes
    lang_votes: Counter = Counter()
    for ext, count in file_ext_counts.items():
        lang = SOURCE_EXTENSIONS.get(ext)
        if lang:
            lang_votes[lang] += count

    # Merge c/c++ — if both present, check for C++ indicators
    c_count = lang_votes.get("c", 0)
    cpp_count = lang_votes.get("c++", 0)
    if c_count and cpp_count:
        # If significant C++ files, call it C++
        if cpp_count > c_count * 0.3:
            lang_votes["c++"] += c_count
            del lang_votes["c"]
        else:
            lang_votes["c"] += cpp_count
            if "c++" in lang_votes:
                del lang_votes["c++"]

    # Merge js/ts — if both, prefer ts
    js_count = lang_votes.get("js", 0)
    ts_count = lang_votes.get("ts", 0)
    if ts_count and js_count:
        lang_votes["ts"] += js_count
        if "js" in lang_votes:
            del lang_votes["js"]

    if not lang_votes:
        return "unknown"

    return lang_votes.most_common(1)[0][0]


# ---------------------------------------------------------------------------
# Build System Detection
# ---------------------------------------------------------------------------

def detect_build_system(workspace: Path) -> str:
    """Detect the build system from files in the workspace root."""
    for filename, system in BUILD_FILES.items():
        if (workspace / filename).exists():
            return system

    # Check for Makefile in subdirs (common for kernel modules)
    if list(workspace.glob("*/Makefile")):
        return "make"

    return "unknown"


# ---------------------------------------------------------------------------
# File Scanning
# ---------------------------------------------------------------------------

def scan_files(workspace: Path, extra_excludes: Set[str]) -> List[Dict[str, Any]]:
    """Scan the workspace and collect file metadata."""
    files = []
    for root, dirs, filenames in os.walk(workspace):
        root_path = Path(root)

        # Prune excluded directories (modifying dirs in-place)
        dirs[:] = [
            d for d in dirs
            if not is_excluded(root_path / d, extra_excludes)
        ]

        for fname in filenames:
            filepath = root_path / fname
            ext = filepath.suffix.lower()
            if ext not in SOURCE_EXTENSIONS:
                continue

            try:
                rel_path = filepath.relative_to(workspace)
            except ValueError:
                continue

            lines = count_lines(filepath)
            files.append({
                "path": str(rel_path),
                "name": fname,
                "ext": ext,
                "dir": relative_dir(filepath, workspace),
                "lines": lines,
                "abs_path": str(filepath),
            })

    return files


# ---------------------------------------------------------------------------
# Directory Tree
# ---------------------------------------------------------------------------

def build_directory_tree(workspace: Path, max_depth: int,
                         extra_excludes: Set[str]) -> str:
    """Build an indented directory tree string."""
    lines = [workspace.name + "/"]

    def _walk(current: Path, prefix: str, depth: int):
        if depth >= max_depth:
            return

        try:
            entries = sorted(current.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return

        # Separate dirs and files
        dirs = [e for e in entries if e.is_dir() and not is_excluded(
            e.relative_to(workspace) if e != workspace else Path(e.name),
            extra_excludes
        )]
        src_files = [e for e in entries if e.is_file() and e.suffix.lower() in SOURCE_EXTENSIONS]

        items = dirs + src_files
        for i, entry in enumerate(items):
            is_last = i == len(items) - 1
            connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
            child_prefix = "    " if is_last else "\u2502   "

            if entry.is_dir():
                # Count source files recursively
                file_count = sum(
                    1 for _ in entry.rglob("*")
                    if _.is_file() and _.suffix.lower() in SOURCE_EXTENSIONS
                )
                if file_count > 0:
                    lines.append(f"{prefix}{connector}{entry.name}/ ({file_count} files)")
                    _walk(entry, prefix + child_prefix, depth + 1)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")

    _walk(workspace, "", 0)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Include/Import Edge Extraction
# ---------------------------------------------------------------------------

def extract_include_edges(files: List[Dict], workspace: Path) -> List[Dict[str, str]]:
    """Extract raw include/import edges between source files (truth only, no clustering).

    For C/C++ includes inside #ifdef/#if blocks, adds a "condition" field
    indicating which CONFIG flag guards the include.
    """
    edges: List[Dict[str, str]] = []
    file_names = {f["name"] for f in files}
    file_paths = {f["path"] for f in files}

    # Regex to track preprocessor conditionals
    ifdef_re = re.compile(
        r"^\s*#\s*(?:ifdef\s+|if\s+defined\s*\(?\s*|if\s+IS_ENABLED\s*\(\s*)"
        r"(CONFIG_\w+)",
        re.MULTILINE,
    )
    endif_re = re.compile(r"^\s*#\s*endif", re.MULTILINE)
    else_re = re.compile(r"^\s*#\s*(?:else|elif)", re.MULTILINE)
    include_re = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.MULTILINE)

    for f in files:
        content = read_head(Path(f["abs_path"]), 16384)

        # Build a position->condition map for C/C++ files
        condition_at: Dict[int, Optional[str]] = {}
        if f["ext"] in (".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"):
            cond_stack: List[Optional[str]] = []
            # Collect all preprocessor directives with positions
            directives: List[tuple] = []
            for m in ifdef_re.finditer(content):
                directives.append((m.start(), "ifdef", m.group(1)))
            for m in endif_re.finditer(content):
                directives.append((m.start(), "endif", None))
            for m in else_re.finditer(content):
                directives.append((m.start(), "else", None))
            directives.sort(key=lambda x: x[0])

            # Walk directives to build condition ranges
            cond_ranges: List[tuple] = []  # (start, end, condition)
            range_start = 0
            for pos, kind, val in directives:
                cur_cond = cond_stack[-1] if cond_stack else None
                cond_ranges.append((range_start, pos, cur_cond))
                if kind == "ifdef":
                    cond_stack.append(val)
                elif kind == "else":
                    if cond_stack:
                        cond_stack[-1] = "!" + cond_stack[-1] if cond_stack[-1] and not cond_stack[-1].startswith("!") else (cond_stack[-1][1:] if cond_stack[-1] and cond_stack[-1].startswith("!") else None)
                elif kind == "endif":
                    if cond_stack:
                        cond_stack.pop()
                range_start = pos

            cond_ranges.append((range_start, len(content), cond_stack[-1] if cond_stack else None))

            # For each include, find its condition
            for m in include_re.finditer(content):
                target = m.group(1)
                target_base = os.path.basename(target)
                if target in file_paths or target_base in file_names:
                    condition = None
                    for rstart, rend, cond in cond_ranges:
                        if rstart <= m.start() < rend:
                            condition = cond
                            break
                    edge: Dict[str, str] = {
                        "from": f["path"],
                        "to": target,
                        "type": "include",
                    }
                    if condition:
                        edge["condition"] = condition
                    edges.append(edge)
        else:
            # Non-C files: simple include extraction
            for m in re.finditer(r'#include\s*"([^"]+)"', content):
                target = m.group(1)
                target_base = os.path.basename(target)
                if target in file_paths or target_base in file_names:
                    edges.append({
                        "from": f["path"],
                        "to": target,
                        "type": "include",
                    })

        # Python imports
        for m in re.finditer(r"^(?:from|import)\s+([\w.]+)", content, re.MULTILINE):
            mod_name = m.group(1).split(".")[0]
            potential = mod_name + ".py"
            if potential in file_names:
                edges.append({
                    "from": f["path"],
                    "to": potential,
                    "type": "import",
                })

    return edges


# ---------------------------------------------------------------------------
# Variant Detection
# ---------------------------------------------------------------------------

def detect_conditional_includes(include_edges: List[Dict[str, str]]) -> List[Dict]:
    """Find pairs of includes guarded by opposite conditions (e.g., hip5.h vs hip4.h).

    Looks for edges from the same file where one has condition CONFIG_X and
    the other has !CONFIG_X, indicating a compile-time variant.
    """
    variants: List[Dict] = []
    # Group conditional includes by source file
    by_source: Dict[str, List[Dict]] = {}
    for edge in include_edges:
        cond = edge.get("condition")
        if cond:
            by_source.setdefault(edge["from"], []).append(edge)

    for src, edges in by_source.items():
        # Look for complementary conditions
        cond_map: Dict[str, Dict] = {}
        for edge in edges:
            cond = edge["condition"]
            cond_map[cond] = edge

        for cond, edge in list(cond_map.items()):
            if cond.startswith("!"):
                positive = cond[1:]
            else:
                positive = cond
                cond = "!" + cond
            if cond in cond_map:
                neg_edge = cond_map[cond]
                # Found a pair: positive config includes one file, else includes another
                variants.append({
                    "type": "conditional_include",
                    "config": positive,
                    "selector_file": src,
                    "when_enabled": edge["to"] if not edge["condition"].startswith("!") else neg_edge["to"],
                    "when_disabled": neg_edge["to"] if not edge["condition"].startswith("!") else edge["to"],
                })
                # Remove the pair so we don't duplicate
                del cond_map[cond]

    return variants


def detect_variant_functions(files: List[Dict], workspace: Path) -> List[Dict]:
    """Find function name pairs suggesting versioned implementations.

    Detects patterns like hip4_init()/hip5_init() defined in different files,
    or transport_v1_send()/transport_v2_send().
    """
    # Collect function definitions per file
    func_re = re.compile(
        r"^(?:static\s+)?(?:inline\s+)?(?:const\s+)?"
        r"(?:void|int|bool|u8|u16|u32|u64|s8|s16|s32|s64|"
        r"unsigned|signed|char|short|long|size_t|ssize_t|"
        r"struct\s+\w+\s*\*?|enum\s+\w+|[\w_]+_t)\s+"
        r"(\w+)\s*\(",
        re.MULTILINE,
    )

    # Version-like suffixes: v1/v2, 4/5, _old/_new, _legacy
    version_re = re.compile(r"^(.+?)(\d+|_v\d+|_old|_new|_legacy|_next)$")

    file_funcs: Dict[str, List[str]] = {}
    for f in files:
        if f["ext"] not in (".c", ".cc", ".cpp", ".cxx"):
            continue
        content = read_head(Path(f["abs_path"]), 32768)
        funcs = func_re.findall(content)
        if funcs:
            file_funcs[f["path"]] = funcs

    # Group functions by their "stem" (name without version suffix)
    stem_map: Dict[str, List[tuple]] = {}  # stem -> [(func_name, file_path)]
    for fpath, funcs in file_funcs.items():
        for func in funcs:
            m = version_re.match(func)
            if m:
                stem = m.group(1)
                suffix = m.group(2)
                stem_map.setdefault(stem, []).append((func, suffix, fpath))

    variants: List[Dict] = []
    seen_stems: Set[str] = set()
    for stem, entries in stem_map.items():
        if len(entries) < 2 or stem in seen_stems:
            continue
        # Only report if functions are in different files
        files_involved = set(e[2] for e in entries)
        if len(files_involved) < 2:
            continue
        seen_stems.add(stem)
        variants.append({
            "type": "function_pair",
            "stem": stem,
            "implementations": [
                {"function": e[0], "suffix": e[1], "file": e[2]}
                for e in entries
            ],
        })

    return variants


def parse_makefile_variants(workspace: Path) -> List[Dict]:
    """Parse Makefile for ifeq/else blocks that select different .o files.

    Detects patterns like:
        ifeq ($(CONFIG_SCSC_WLAN_HIP5),y)
        scsc_wlan-$(CONFIG_SCSC_WLAN) += hip5.o
        else
        scsc_wlan-$(CONFIG_SCSC_WLAN) += hip4.o
        endif
    """
    makefile = workspace / "Makefile"
    if not makefile.exists():
        # Try alternate names
        for alt in ("makefile", "GNUmakefile"):
            alt_path = workspace / alt
            if alt_path.exists():
                makefile = alt_path
                break
        else:
            return []

    try:
        content = makefile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    variants: List[Dict] = []
    ifeq_re = re.compile(
        r"^\s*ifeq\s+\(\$\((\w+)\)\s*,\s*(\w+)\)",
        re.MULTILINE,
    )

    obj_re = re.compile(r"[\w-]+-\$\(\w+\)\s*\+=\s*(\S+\.o)")
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        m = ifeq_re.match(lines[i])
        if m:
            config = m.group(1)
            # Collect .o targets in the if-branch and else-branch
            if_objs: List[str] = []
            else_objs: List[str] = []
            in_else = False
            depth = 1
            j = i + 1
            while j < len(lines) and depth > 0:
                line = lines[j].strip()
                if line.startswith("ifeq") or line.startswith("ifneq") or line.startswith("ifdef") or line.startswith("ifndef"):
                    depth += 1
                elif line == "endif":
                    depth -= 1
                    if depth == 0:
                        break
                elif line == "else" and depth == 1:
                    in_else = True
                    j += 1
                    continue

                if depth == 1:
                    om = obj_re.search(lines[j])
                    if om:
                        obj_name = om.group(1)
                        src_name = obj_name.replace(".o", ".c")
                        if in_else:
                            else_objs.append(src_name)
                        else:
                            if_objs.append(src_name)
                j += 1

            if if_objs and else_objs:
                variants.append({
                    "type": "makefile_conditional",
                    "config": config,
                    "when_enabled": if_objs,
                    "when_disabled": else_objs,
                })
        i += 1

    return variants


def detect_variants(files: List[Dict], workspace: Path,
                    include_edges: List[Dict[str, str]]) -> List[Dict]:
    """Combine all variant detection signals into a unified list."""
    variants: List[Dict] = []
    variants.extend(detect_conditional_includes(include_edges))
    variants.extend(detect_variant_functions(files, workspace))
    variants.extend(parse_makefile_variants(workspace))
    return variants


# ---------------------------------------------------------------------------
# Key File Identification
# ---------------------------------------------------------------------------

def find_key_files(workspace: Path, files: List[Dict],
                   build_system: str) -> List[Dict]:
    """Identify the most important files in the codebase."""
    key: List[Dict] = []
    seen_paths: Set[str] = set()

    seen_inodes: Set[int] = set()

    def add_key(path: str, lines: int, reason: str):
        if path in seen_paths or len(key) >= MAX_KEY_FILES:
            return
        # Deduplicate by inode (handles case-insensitive filesystems)
        try:
            inode = (workspace / path).stat().st_ino
            if inode in seen_inodes:
                return
            seen_inodes.add(inode)
        except OSError:
            pass
        seen_paths.add(path)
        key.append({"path": path, "lines": lines, "reason": reason})

    # Build files
    for fname, system in BUILD_FILES.items():
        bf = workspace / fname
        if bf.exists():
            add_key(fname, count_lines(bf), f"build system ({system})")

    # Config files (scan root only)
    for entry in sorted(workspace.iterdir()):
        if not entry.is_file():
            continue
        name = entry.name
        for pattern in CONFIG_PATTERNS:
            if "*" in pattern:
                import fnmatch
                if fnmatch.fnmatch(name, pattern):
                    add_key(name, count_lines(entry), "config file")
                    break
            elif name == pattern:
                add_key(name, count_lines(entry), "config file")
                break

    # Largest source files (top 10)
    sorted_by_size = sorted(files, key=lambda f: f["lines"], reverse=True)
    for f in sorted_by_size[:10]:
        add_key(f["path"], f["lines"], "largest source file")

    # Central headers — files included by many source files
    include_counts: Counter = Counter()
    source_files = [f for f in files if f["ext"] in (".c", ".cc", ".cpp", ".cxx")]
    for sf in source_files[:200]:  # Cap to avoid slow scanning
        content = read_head(Path(sf["abs_path"]), 8192)
        for m in re.finditer(r'#include\s*"([^"]+)"', content):
            include_counts[os.path.basename(m.group(1))] += 1

    if source_files:
        threshold = max(len(source_files) * 0.3, 3)
        for header, count in include_counts.most_common(10):
            if count >= threshold:
                hfiles = [f for f in files if f["name"] == header]
                for hf in hfiles:
                    add_key(hf["path"], hf["lines"],
                            f"central header (included by {count}/{len(source_files)} source files)")

    # Existing documentation
    for doc in workspace.glob("*.md"):
        add_key(doc.name, count_lines(doc), "documentation")
    for doc in workspace.glob("docs/*.md"):
        try:
            rel = doc.relative_to(workspace)
            add_key(str(rel), count_lines(doc), "documentation")
        except ValueError:
            pass

    return key


# ---------------------------------------------------------------------------
# Entry Point Detection
# ---------------------------------------------------------------------------

def find_entry_points(workspace: Path, files: List[Dict],
                      language: str) -> List[Dict]:
    """Find likely entry point functions/files."""
    entries: List[Dict] = []

    # Check index files
    index_names = INDEX_FILES.get(language, [])
    for f in files:
        if f["name"] in index_names:
            entries.append({"path": f["path"], "type": "index file"})

    # Search for entry point patterns in source files
    patterns = ENTRY_POINT_PATTERNS.get(language, [])
    if not patterns:
        return entries[:MAX_ENTRY_POINTS]

    for f in files:
        if len(entries) >= MAX_ENTRY_POINTS:
            break
        filepath = Path(f["abs_path"])
        # Read head of file; also read tail for kernel modules where
        # module_init/module_exit are conventionally at the end
        content = read_head(filepath, 16384)
        if f["lines"] > 200:
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(0, 2)  # end
                    size = fh.tell()
                    tail_start = max(0, size - 4096)
                    fh.seek(tail_start)
                    tail = fh.read()
                    if tail_start > 16384:
                        content = content + "\n" + tail
            except (OSError, IOError):
                pass

        for pat in patterns:
            m = pat.search(content)
            if m:
                # Extract function name if captured
                name = m.group(1) if m.lastindex else m.group(0).strip()
                entries.append({
                    "path": f["path"],
                    "type": "entry point",
                    "symbol": name[:80],
                })
                break

    return entries[:MAX_ENTRY_POINTS]


# ---------------------------------------------------------------------------
# Config File Discovery
# ---------------------------------------------------------------------------

def find_config_files(workspace: Path) -> List[str]:
    """Find configuration files in the workspace."""
    configs: List[str] = []
    for entry in sorted(workspace.iterdir()):
        if not entry.is_file():
            continue
        name = entry.name
        for pattern in CONFIG_PATTERNS:
            if "*" in pattern:
                import fnmatch
                if fnmatch.fnmatch(name, pattern):
                    configs.append(name)
                    break
            elif name == pattern:
                configs.append(name)
                break

    # Also check for Kconfig, Kbuild in root
    for special in ("Kconfig", "Kbuild", ".config"):
        if (workspace / special).exists() and special not in configs:
            configs.append(special)

    return configs


# ---------------------------------------------------------------------------
# Existing Documentation
# ---------------------------------------------------------------------------

def find_existing_docs(workspace: Path) -> List[str]:
    """Find existing documentation files."""
    docs: List[str] = []
    for md in sorted(workspace.glob("*.md")):
        docs.append(md.name)
    for md in sorted(workspace.glob("docs/*.md")):
        try:
            docs.append(str(md.relative_to(workspace)))
        except ValueError:
            pass
    for md in sorted(workspace.glob("doc/*.md")):
        try:
            docs.append(str(md.relative_to(workspace)))
        except ValueError:
            pass
    # README variants
    for name in ("README", "README.txt", "README.rst"):
        if (workspace / name).exists() and name not in docs:
            docs.append(name)
    return docs


# ---------------------------------------------------------------------------
# Text Formatter
# ---------------------------------------------------------------------------

def format_text(data: Dict[str, Any]) -> str:
    """Format analysis data as human-readable text."""
    lines = []
    lines.append(f"=== Codebase Analysis: {data['project_name']} ===\n")
    lines.append(f"Workspace:    {data['workspace']}")
    lines.append(f"Language:     {data['language']}")
    lines.append(f"Build system: {data['build_system']}")

    stats = data["stats"]
    lines.append(f"\n--- Statistics ---")
    lines.append(f"Total files:  {stats['total_files']}")
    lines.append(f"Total lines:  {stats['total_lines']:,}")
    lines.append(f"\nBy extension:")
    for ext, count in sorted(stats["by_extension"].items(), key=lambda x: -x[1]):
        lines.append(f"  {ext:8s} {count:5d} files")
    lines.append(f"\nBy directory:")
    for d, count in sorted(stats["by_directory"].items(), key=lambda x: -x[1])[:20]:
        lines.append(f"  {d:30s} {count:5d} files")

    lines.append(f"\n--- Directory Tree ---")
    lines.append(data["directory_tree"])

    include_edges = data.get("include_edges", [])
    lines.append(f"\n--- Include/Import Edges ({len(include_edges)}) ---")
    for edge in include_edges[:100]:
        cond = edge.get("condition", "")
        cond_str = f"  [if {cond}]" if cond else ""
        lines.append(f"  {edge['from']} --{edge['type']}--> {edge['to']}{cond_str}")
    if len(include_edges) > 100:
        lines.append(f"  ... and {len(include_edges) - 100} more edges")

    variants = data.get("variants", [])
    if variants:
        lines.append(f"\n--- Compile-time Variants ({len(variants)}) ---")
        for v in variants:
            vtype = v["type"]
            if vtype == "conditional_include":
                lines.append(f"  [{v['config']}] {v['selector_file']}: "
                             f"{v['when_enabled']} (enabled) vs {v['when_disabled']} (disabled)")
            elif vtype == "makefile_conditional":
                lines.append(f"  [{v['config']}] Makefile: "
                             f"{', '.join(v['when_enabled'])} (enabled) vs "
                             f"{', '.join(v['when_disabled'])} (disabled)")
            elif vtype == "function_pair":
                impls = ", ".join(f"{i['function']} in {i['file']}" for i in v["implementations"])
                lines.append(f"  [function pair] stem={v['stem']}: {impls}")

    lines.append(f"\n--- Key Files ({len(data['key_files'])}) ---")
    for kf in data["key_files"]:
        lines.append(f"  {kf['path']:40s} {kf['lines']:6d} lines  ({kf['reason']})")

    lines.append(f"\n--- Entry Points ---")
    for ep in data["entry_points"]:
        symbol = ep.get("symbol", "")
        lines.append(f"  {ep['path']:40s} {ep['type']}  {symbol}")

    lines.append(f"\n--- Config Files ---")
    for cf in data["config_files"]:
        lines.append(f"  {cf}")

    lines.append(f"\n--- Existing Docs ---")
    for doc in data["existing_docs"]:
        lines.append(f"  {doc}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main Analysis
# ---------------------------------------------------------------------------

def analyze(workspace: Path, max_depth: int = 4,
            extra_excludes: Optional[Set[str]] = None,
            language_override: Optional[str] = None) -> Dict[str, Any]:
    """Run the full codebase analysis."""
    if extra_excludes is None:
        extra_excludes = set()

    workspace = workspace.resolve()
    if not workspace.is_dir():
        print(f"Error: '{workspace}' is not a directory", file=sys.stderr)
        sys.exit(1)

    # Scan files
    files = scan_files(workspace, extra_excludes)
    if not files:
        print(f"Error: No source files found in '{workspace}'", file=sys.stderr)
        sys.exit(1)

    # Statistics
    ext_counts: Counter = Counter()
    dir_counts: Counter = Counter()
    total_lines = 0
    for f in files:
        ext_counts[f["ext"]] += 1
        dir_counts[f["dir"]] += 1
        total_lines += f["lines"]

    # Language detection
    if language_override and language_override != "auto":
        language = language_override
    else:
        language = detect_language(workspace, ext_counts)

    # Build system
    build_system = detect_build_system(workspace)

    # Directory tree
    tree = build_directory_tree(workspace, max_depth, extra_excludes)

    # Include/import edges
    include_edges = extract_include_edges(files, workspace)

    # Variant detection
    variants = detect_variants(files, workspace, include_edges)

    # Key files
    key_files = find_key_files(workspace, files, build_system)

    # Entry points
    entry_points = find_entry_points(workspace, files, language)

    # Config files
    config_files = find_config_files(workspace)

    # Existing docs
    existing_docs = find_existing_docs(workspace)

    # Build files list without abs_path (internal-only field)
    files_output = [
        {"path": f["path"], "name": f["name"], "ext": f["ext"],
         "dir": f["dir"], "lines": f["lines"]}
        for f in files
    ]

    return {
        "project_name": workspace.name,
        "workspace": str(workspace),
        "language": language,
        "build_system": build_system,
        "stats": {
            "total_files": len(files),
            "total_lines": total_lines,
            "by_extension": dict(ext_counts.most_common()),
            "by_directory": dict(dir_counts.most_common()),
        },
        "files": files_output,
        "directory_tree": tree,
        "include_edges": include_edges,
        "variants": variants,
        "key_files": key_files,
        "entry_points": entry_points,
        "config_files": config_files,
        "existing_docs": existing_docs,
    }


# ---------------------------------------------------------------------------
# Diff Analysis
# ---------------------------------------------------------------------------

def diff_analysis(old_path: Path, new_path: Path) -> Dict[str, Any]:
    """Compare two analysis.json files and produce a structured delta.

    Returns a dict with new/deleted/modified files, edge changes, and stats delta.
    """
    with open(old_path, "r", encoding="utf-8") as f:
        old = json.load(f)
    with open(new_path, "r", encoding="utf-8") as f:
        new = json.load(f)

    # Build file lookup by path
    old_files = {f["path"]: f for f in old.get("files", [])}
    new_files = {f["path"]: f for f in new.get("files", [])}

    old_paths = set(old_files.keys())
    new_paths = set(new_files.keys())

    # New, deleted, modified files
    new_file_list = [
        {"path": p, "lines": new_files[p]["lines"]}
        for p in sorted(new_paths - old_paths)
    ]
    deleted_file_list = [
        {"path": p, "lines": old_files[p]["lines"]}
        for p in sorted(old_paths - new_paths)
    ]
    modified_file_list = [
        {"path": p, "old_lines": old_files[p]["lines"], "new_lines": new_files[p]["lines"]}
        for p in sorted(old_paths & new_paths)
        if old_files[p]["lines"] != new_files[p]["lines"]
    ]

    # Edge differences
    old_edges = {(e["from"], e["to"], e["type"]) for e in old.get("include_edges", [])}
    new_edges = {(e["from"], e["to"], e["type"]) for e in new.get("include_edges", [])}

    new_include_edges = [
        {"from": e[0], "to": e[1], "type": e[2]}
        for e in sorted(new_edges - old_edges)
    ]
    removed_include_edges = [
        {"from": e[0], "to": e[1], "type": e[2]}
        for e in sorted(old_edges - new_edges)
    ]

    # Variant differences
    def _variant_key(v: Dict) -> str:
        if v["type"] == "conditional_include":
            return f"ci:{v['config']}:{v['selector_file']}"
        elif v["type"] == "makefile_conditional":
            return f"mk:{v['config']}"
        elif v["type"] == "function_pair":
            return f"fp:{v['stem']}"
        return json.dumps(v, sort_keys=True)

    old_variants = {_variant_key(v): v for v in old.get("variants", [])}
    new_variants = {_variant_key(v): v for v in new.get("variants", [])}
    new_variant_list = [new_variants[k] for k in sorted(set(new_variants) - set(old_variants))]
    removed_variant_list = [old_variants[k] for k in sorted(set(old_variants) - set(new_variants))]

    # Stats delta
    old_stats = old.get("stats", {})
    new_stats = new.get("stats", {})
    stats_delta = {
        "total_files": new_stats.get("total_files", 0) - old_stats.get("total_files", 0),
        "total_lines": new_stats.get("total_lines", 0) - old_stats.get("total_lines", 0),
    }

    return {
        "new_files": new_file_list,
        "deleted_files": deleted_file_list,
        "modified_files": modified_file_list,
        "new_include_edges": new_include_edges,
        "removed_include_edges": removed_include_edges,
        "new_variants": new_variant_list,
        "removed_variants": removed_variant_list,
        "stats_delta": stats_delta,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run_diff(args):
    """Handle the 'diff' subcommand."""
    delta = diff_analysis(Path(args.old_analysis), Path(args.new_analysis))
    output = json.dumps(delta, indent=2, ensure_ascii=False)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Diff written to {out_path}", file=sys.stderr)
    else:
        print(output)


def _run_analyze(args):
    """Handle the 'analyze' subcommand (or default invocation)."""
    workspace = Path(args.workspace)
    data = analyze(
        workspace,
        max_depth=args.max_depth,
        extra_excludes=set(args.exclude) if args.exclude else None,
        language_override=args.language if args.language != "auto" else None,
    )

    if args.format == "json":
        output = json.dumps(data, indent=2, ensure_ascii=False)
    else:
        output = format_text(data)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Analysis written to {out_path}", file=sys.stderr)
    else:
        print(output)


def main():
    # Check if first positional arg is 'diff'; otherwise fall back to
    # original single-parser CLI for backwards compatibility.
    if len(sys.argv) >= 2 and sys.argv[1] == "diff":
        parser = argparse.ArgumentParser(
            prog="analyze.py diff",
            description="Compare two analysis.json files and output the delta.",
        )
        parser.add_argument("old_analysis", help="Path to the old analysis.json")
        parser.add_argument("new_analysis", help="Path to the new analysis.json")
        parser.add_argument("--output", help="Write output to file instead of stdout")
        args = parser.parse_args(sys.argv[2:])
        _run_diff(args)
    else:
        parser = argparse.ArgumentParser(
            description="Analyze a codebase for architecture documentation generation."
        )
        parser.add_argument("workspace", help="Path to the workspace to analyze")
        parser.add_argument("--format", choices=["json", "text"], default="json",
                            help="Output format (default: json)")
        parser.add_argument("--max-depth", type=int, default=4,
                            help="Directory tree depth (default: 4)")
        parser.add_argument("--exclude", nargs="*", default=[],
                            help="Extra directories to exclude")
        parser.add_argument("--language", default="auto",
                            choices=["c", "c++", "python", "java", "go", "rust",
                                     "js", "ts", "ruby", "swift", "shell", "auto"],
                            help="Override language detection (default: auto)")
        parser.add_argument("--output", help="Write output to file instead of stdout")
        args = parser.parse_args()
        _run_analyze(args)


if __name__ == "__main__":
    main()
