#!/usr/bin/env python3
"""Codebase analyzer for the codebase-explainer-generator skill.

Gathers structured data about a codebase that Claude uses to write
architecture documentation. Pure Python 3.8+, no external packages.

Usage:
    python3 analyze.py <workspace> [options]
    python3 analyze.py /path/to/project --format json --output analysis.json
    python3 analyze.py /path/to/project --format text
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_MODULES = 30
MAX_FILES_PER_MODULE = 50
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
# Module Detection
# ---------------------------------------------------------------------------

def detect_modules_from_makefile(workspace: Path, files: List[Dict]) -> List[Dict]:
    """Strategy 1: Parse Makefile obj- targets (for C/kernel modules)."""
    modules = []
    makefile = workspace / "Makefile"
    if not makefile.exists():
        return modules

    content = read_head(makefile, 65536)
    file_set = {f["name"] for f in files}

    # Match obj-$(CONFIG_...) += name.o or obj-y += name.o
    obj_pattern = re.compile(
        r"^[\w-]*obj-[^\s]*\s*[+:]?=\s*(.+?)$", re.MULTILINE
    )
    for match in obj_pattern.finditer(content):
        targets = match.group(1).strip().split()
        for target in targets:
            target = target.strip().rstrip("\\")
            if not target or target.startswith("#"):
                continue
            # name.o → look for name.c and name.h
            base = target.replace(".o", "")
            if not base:
                continue

            # Check if this is a directory-based module (name/)
            module_dir = workspace / base
            if module_dir.is_dir():
                dir_files = [
                    f for f in files
                    if f["dir"].startswith(base + "/") or f["dir"] == base + "/"
                ]
                if dir_files:
                    modules.append({
                        "name": base,
                        "files": [f["path"] for f in dir_files[:MAX_FILES_PER_MODULE]],
                        "line_count": sum(f["lines"] for f in dir_files),
                        "reason": f"Makefile obj target: {base}/",
                        "truncated": len(dir_files) > MAX_FILES_PER_MODULE,
                    })
                continue

            # Single-file module: find matching source files
            matched = [
                f for f in files
                if f["name"].startswith(base) and f["dir"] == "./"
            ]
            if matched:
                modules.append({
                    "name": base,
                    "files": [f["path"] for f in matched[:MAX_FILES_PER_MODULE]],
                    "line_count": sum(f["lines"] for f in matched),
                    "reason": f"Makefile obj target: {target}",
                    "truncated": len(matched) > MAX_FILES_PER_MODULE,
                })

    return modules


def detect_modules_from_cargo(workspace: Path, files: List[Dict]) -> List[Dict]:
    """Parse Cargo.toml workspace members."""
    modules = []
    cargo = workspace / "Cargo.toml"
    if not cargo.exists():
        return modules

    content = read_head(cargo)
    in_members = False
    for line in content.split("\n"):
        if "[workspace]" in line:
            in_members = False
        if "members" in line and "=" in line:
            in_members = True
            continue
        if in_members:
            m = re.match(r'\s*"([^"]+)"', line)
            if m:
                member = m.group(1)
                member_files = [
                    f for f in files
                    if f["path"].startswith(member + "/") or f["dir"].startswith(member + "/")
                ]
                if member_files:
                    modules.append({
                        "name": member,
                        "files": [f["path"] for f in member_files[:MAX_FILES_PER_MODULE]],
                        "line_count": sum(f["lines"] for f in member_files),
                        "reason": f"Cargo workspace member",
                        "truncated": len(member_files) > MAX_FILES_PER_MODULE,
                    })
            if "]" in line:
                in_members = False

    return modules


def detect_modules_from_package_json(workspace: Path, files: List[Dict]) -> List[Dict]:
    """Parse package.json workspaces."""
    modules = []
    pkg = workspace / "package.json"
    if not pkg.exists():
        return modules

    try:
        with open(pkg) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return modules

    workspaces = data.get("workspaces", [])
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages", [])

    for ws in workspaces:
        ws_clean = ws.rstrip("/*")
        if "*" in ws_clean:
            continue  # Skip glob patterns
        ws_files = [f for f in files if f["path"].startswith(ws_clean + "/")]
        if ws_files:
            modules.append({
                "name": ws_clean,
                "files": [f["path"] for f in ws_files[:MAX_FILES_PER_MODULE]],
                "line_count": sum(f["lines"] for f in ws_files),
                "reason": "package.json workspace",
                "truncated": len(ws_files) > MAX_FILES_PER_MODULE,
            })

    return modules


def detect_modules_by_directory(files: List[Dict], workspace: Path) -> List[Dict]:
    """Strategy 2: Top-level subdirectories with >3 source files."""
    modules = []
    dir_files: Dict[str, List[Dict]] = defaultdict(list)

    for f in files:
        parts = Path(f["path"]).parts
        if len(parts) > 1:
            top_dir = parts[0]
            dir_files[top_dir].append(f)

    for dirname, dfiles in sorted(dir_files.items()):
        if len(dfiles) >= 3:
            modules.append({
                "name": dirname,
                "files": [f["path"] for f in dfiles[:MAX_FILES_PER_MODULE]],
                "line_count": sum(f["lines"] for f in dfiles),
                "reason": f"directory cluster: {dirname}/ ({len(dfiles)} files)",
                "truncated": len(dfiles) > MAX_FILES_PER_MODULE,
            })

    return modules


def detect_modules_by_prefix(files: List[Dict]) -> List[Dict]:
    """Strategy 3: File-prefix clustering for flat directory layouts."""
    modules = []
    # Only look at root-level files
    root_files = [f for f in files if f["dir"] == "./"]
    if len(root_files) < 5:
        return modules

    # Extract prefixes: take the part before first digit, underscore-delimited chunk,
    # or the stem if short
    prefix_groups: Dict[str, List[Dict]] = defaultdict(list)
    for f in root_files:
        stem = Path(f["name"]).stem
        # Try splitting on digits: hip4.c, hip5.c → "hip"
        m = re.match(r"^([a-zA-Z_]+?)(?:\d|_\d)", stem)
        if m and len(m.group(1)) >= 2:
            prefix_groups[m.group(1)].append(f)
        else:
            # Try splitting on underscores: sap_mlme.c, sap_ma.c → "sap"
            parts = stem.split("_")
            if len(parts) >= 2 and len(parts[0]) >= 2:
                prefix_groups[parts[0]].append(f)

    for prefix, pfiles in sorted(prefix_groups.items()):
        if len(pfiles) >= 2:
            modules.append({
                "name": f"{prefix}-subsystem",
                "files": [f["path"] for f in pfiles[:MAX_FILES_PER_MODULE]],
                "line_count": sum(f["lines"] for f in pfiles),
                "reason": f"file-prefix cluster: {prefix}*",
                "truncated": len(pfiles) > MAX_FILES_PER_MODULE,
            })

    return modules


def detect_modules_by_naming(files: List[Dict]) -> List[Dict]:
    """Strategy 4: Naming conventions like scsc_wifilogger_*, nl80211_vendor*."""
    modules = []
    root_files = [f for f in files if f["dir"] == "./"]
    if not root_files:
        return modules

    # Look for multi-word prefixes (compound naming)
    compound_groups: Dict[str, List[Dict]] = defaultdict(list)
    for f in root_files:
        stem = Path(f["name"]).stem
        parts = stem.split("_")
        if len(parts) >= 3:
            # Use first two segments as the compound prefix
            compound = "_".join(parts[:2])
            if len(compound) >= 4:
                compound_groups[compound].append(f)

    for compound, cfiles in sorted(compound_groups.items()):
        if len(cfiles) >= 2:
            modules.append({
                "name": compound.replace("_", "-"),
                "files": [f["path"] for f in cfiles[:MAX_FILES_PER_MODULE]],
                "line_count": sum(f["lines"] for f in cfiles),
                "reason": f"naming convention: {compound}_*",
                "truncated": len(cfiles) > MAX_FILES_PER_MODULE,
            })

    return modules


def detect_modules_by_includes(files: List[Dict], workspace: Path) -> List[Dict]:
    """Strategy 5: Cluster by include/import relationships (flat directory fallback)."""
    modules = []
    root_files = [f for f in files if f["dir"] == "./"]
    if len(root_files) < 3:
        return modules

    # Build include graph for C/C++ or import graph for Python
    include_map: Dict[str, Set[str]] = defaultdict(set)
    file_names = {f["name"] for f in root_files}

    for f in root_files:
        content = read_head(Path(f["abs_path"]), 16384)
        # C/C++ includes
        for m in re.finditer(r'#include\s*"([^"]+)"', content):
            included = os.path.basename(m.group(1))
            if included in file_names:
                include_map[f["name"]].add(included)
                include_map[included].add(f["name"])
        # Python imports
        for m in re.finditer(r"^(?:from|import)\s+([\w.]+)", content, re.MULTILINE):
            mod_name = m.group(1).split(".")[0]
            potential = mod_name + ".py"
            if potential in file_names:
                include_map[f["name"]].add(potential)
                include_map[potential].add(f["name"])

    # Find connected components
    visited: Set[str] = set()
    clusters: List[Set[str]] = []

    def dfs(node: str, cluster: Set[str]):
        cluster.add(node)
        visited.add(node)
        for neighbor in include_map.get(node, set()):
            if neighbor not in visited:
                dfs(neighbor, cluster)

    for fname in include_map:
        if fname not in visited:
            cluster: Set[str] = set()
            dfs(fname, cluster)
            if len(cluster) >= 3:
                clusters.append(cluster)

    for i, cluster in enumerate(sorted(clusters, key=len, reverse=True)):
        cluster_files = [f for f in root_files if f["name"] in cluster]
        # Try to name the cluster from the most common prefix
        stems = [Path(f["name"]).stem for f in cluster_files]
        common = os.path.commonprefix(stems)
        name = common.rstrip("_") if len(common) >= 2 else f"cluster-{i+1}"

        modules.append({
            "name": name,
            "files": [f["path"] for f in cluster_files[:MAX_FILES_PER_MODULE]],
            "line_count": sum(f["lines"] for f in cluster_files),
            "reason": f"include-graph cluster ({len(cluster)} connected files)",
            "truncated": len(cluster_files) > MAX_FILES_PER_MODULE,
        })

    return modules


def merge_modules(all_modules: List[Dict]) -> List[Dict]:
    """Merge overlapping modules, preferring earlier (higher priority) detections."""
    if not all_modules:
        return []

    assigned_files: Set[str] = set()
    merged: List[Dict] = []

    for mod in all_modules:
        # Remove files already assigned to a higher-priority module
        remaining = [f for f in mod["files"] if f not in assigned_files]
        if len(remaining) < 2:
            continue

        assigned_files.update(remaining)
        merged.append({
            "name": mod["name"],
            "files": remaining[:MAX_FILES_PER_MODULE],
            "line_count": mod["line_count"],
            "reason": mod["reason"],
            "truncated": mod.get("truncated", False) or len(remaining) > MAX_FILES_PER_MODULE,
        })

    return merged[:MAX_MODULES]


def detect_modules(workspace: Path, files: List[Dict],
                   build_system: str) -> Tuple[List[Dict], bool]:
    """Run all module detection heuristics in priority order."""
    all_modules: List[Dict] = []

    # Strategy 1: Build targets
    if build_system in ("make", "kbuild"):
        all_modules.extend(detect_modules_from_makefile(workspace, files))
    elif build_system == "cargo":
        all_modules.extend(detect_modules_from_cargo(workspace, files))
    elif build_system == "npm":
        all_modules.extend(detect_modules_from_package_json(workspace, files))

    # Strategy 2: Directory clustering
    all_modules.extend(detect_modules_by_directory(files, workspace))

    # Strategy 3: File-prefix clustering
    all_modules.extend(detect_modules_by_prefix(files))

    # Strategy 4: Naming convention
    all_modules.extend(detect_modules_by_naming(files))

    # Strategy 5: Include/import relationships (fallback)
    if len(all_modules) < 3:
        all_modules.extend(detect_modules_by_includes(files, workspace))

    merged = merge_modules(all_modules)
    truncated = len(all_modules) > MAX_MODULES
    return merged, truncated


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

    lines.append(f"\n--- Modules ({len(data['modules'])}) ---")
    if data.get("modules_truncated"):
        lines.append(f"  (truncated to {MAX_MODULES}, more modules exist)")
    for mod in data["modules"]:
        lines.append(f"\n  [{mod['name']}] ({mod['line_count']:,} lines, {len(mod['files'])} files)")
        lines.append(f"    Reason: {mod['reason']}")
        for fp in mod["files"][:10]:
            lines.append(f"    - {fp}")
        if len(mod["files"]) > 10:
            lines.append(f"    ... and {len(mod['files']) - 10} more")

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

    # Module detection
    modules, modules_truncated = detect_modules(workspace, files, build_system)

    # Key files
    key_files = find_key_files(workspace, files, build_system)

    # Entry points
    entry_points = find_entry_points(workspace, files, language)

    # Config files
    config_files = find_config_files(workspace)

    # Existing docs
    existing_docs = find_existing_docs(workspace)

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
        "directory_tree": tree,
        "modules": modules,
        "modules_truncated": modules_truncated,
        "key_files": key_files,
        "entry_points": entry_points,
        "config_files": config_files,
        "existing_docs": existing_docs,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
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


if __name__ == "__main__":
    main()
