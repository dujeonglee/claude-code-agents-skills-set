#!/usr/bin/env python3
"""Run Doxygen to produce HTML and XML documentation.

Usage:
    python3 generate.py <workspace> [options]

Examples:
    python3 generate.py /path/to/project
    python3 generate.py /path/to/project --language c++ --project-name MyLib
    python3 generate.py /path/to/project --no-graphs --force
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Allow importing sibling modules
sys.path.insert(0, str(Path(__file__).resolve().parent))

import platform as plat_mod
import doxyfile_template


CACHE_REL_PATH = ".claude/skill-cache/doxygen-generator.json"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate Doxygen documentation for a workspace."
    )
    parser.add_argument("workspace", help="Path to the workspace/project root.")
    parser.add_argument("--project-name", default=None,
                        help="Project name for docs (default: directory name).")
    parser.add_argument("--output-dir", default=".doxygen",
                        help="Output directory relative to workspace (default: .doxygen).")
    parser.add_argument("--input-dirs", nargs="*", default=None,
                        help="Directories to scan (default: workspace root).")
    parser.add_argument("--language", default="c",
                        choices=["c", "c++", "java", "python", "auto"],
                        help="Source language (default: c).")
    parser.add_argument("--file-patterns", default=None,
                        help="Space-separated file patterns (overrides language default).")
    parser.add_argument("--exclude", nargs="*", default=None,
                        help="Directories to exclude.")
    parser.add_argument("--doxyfile", default=None,
                        help="Use a custom Doxyfile instead of generating one.")
    parser.add_argument("--no-html", action="store_true",
                        help="Skip HTML generation.")
    parser.add_argument("--no-xml", action="store_true",
                        help="Skip XML generation.")
    parser.add_argument("--no-graphs", action="store_true",
                        help="Disable call/caller graph generation.")
    parser.add_argument("--force", action="store_true",
                        help="Force regeneration even if output is up-to-date.")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Clear cached state and regenerate.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output.")
    return parser.parse_args(argv)


def load_cache(workspace: Path) -> dict:
    cache_path = workspace / CACHE_REL_PATH
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cache(workspace: Path, data: dict) -> None:
    cache_path = workspace / CACHE_REL_PATH
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, indent=2))


def get_newest_source_mtime(workspace: Path, input_dirs: list[str],
                            file_patterns: str) -> float:
    """Find the newest modification time among source files."""
    patterns = file_patterns.split()
    newest = 0.0
    for input_dir in input_dirs:
        d = Path(input_dir)
        if not d.exists():
            continue
        for pattern in patterns:
            for f in d.rglob(pattern):
                try:
                    mt = f.stat().st_mtime
                    if mt > newest:
                        newest = mt
                except OSError:
                    continue
    return newest


def is_stale(workspace: Path, output_dir: Path, input_dirs: list[str],
             file_patterns: str, cache: dict) -> bool:
    """Check if documentation needs regeneration."""
    html_index = output_dir / "html" / "index.html"
    xml_index = output_dir / "xml" / "index.xml"

    if not html_index.exists() and not xml_index.exists():
        return True

    cached_ts = cache.get("generated_at", 0)
    if cached_ts == 0:
        return True

    newest = get_newest_source_mtime(workspace, input_dirs, file_patterns)
    return newest > cached_ts


def detect_gatekeeper_error(stderr: str) -> bool:
    """Detect macOS Gatekeeper blocking errors."""
    indicators = [
        "cannot be opened because the developer cannot be verified",
        "not opened because it is from an unidentified developer",
        "quarantine",
    ]
    return any(ind in stderr.lower() for ind in indicators)


def main(argv=None):
    args = parse_args(argv)
    workspace = Path(args.workspace).resolve()

    if not workspace.is_dir():
        print(f"Error: Workspace not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    # Resolve paths
    output_dir = workspace / args.output_dir
    project_name = args.project_name or workspace.name
    input_dirs = [str(Path(d).resolve()) for d in args.input_dirs] if args.input_dirs else [str(workspace)]
    language = args.language
    file_patterns = args.file_patterns or doxyfile_template.LANGUAGE_PATTERNS.get(
        language, doxyfile_template.LANGUAGE_PATTERNS["auto"]
    )
    # Flatten exclude args: handle both --exclude "a b" and --exclude a b
    raw_excludes = args.exclude or []
    exclude_dirs = []
    for item in raw_excludes:
        exclude_dirs.extend(item.split())

    # Platform detection
    try:
        plat = plat_mod.detect_platform()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"Platform: {plat}")
        print(f"Workspace: {workspace}")
        print(f"Output: {output_dir}")

    # Resolve binaries
    doxygen_path = plat_mod.get_doxygen_path()
    dot_path = plat_mod.get_dot_path()
    dot_dir = plat_mod.get_dot_dir()

    if not doxygen_path.exists():
        print(f"Error: Doxygen binary not found at: {doxygen_path}", file=sys.stderr)
        print(f"Platform detected: {plat}", file=sys.stderr)
        print(f"Please place the doxygen binary in: {doxygen_path}", file=sys.stderr)
        print(f"See bin/README.md for download instructions.", file=sys.stderr)
        sys.exit(1)

    plat_mod.ensure_executable(doxygen_path)

    # Check dot availability
    have_dot = dot_path.exists() and not args.no_graphs
    if have_dot:
        plat_mod.ensure_executable(dot_path)
    elif not args.no_graphs:
        print("Warning: dot (Graphviz) not found. Graphs will be disabled.", file=sys.stderr)
        print(f"Expected at: {dot_path}", file=sys.stderr)

    # Cache handling
    if args.clear_cache:
        save_cache(workspace, {})

    cache = load_cache(workspace)

    if not args.force and not is_stale(workspace, output_dir, input_dirs, file_patterns, cache):
        print("Documentation is up-to-date. Use --force to regenerate.")
        return

    # Generate or use custom Doxyfile
    if args.doxyfile:
        doxyfile_path = Path(args.doxyfile).resolve()
        if not doxyfile_path.exists():
            print(f"Error: Custom Doxyfile not found: {doxyfile_path}", file=sys.stderr)
            sys.exit(1)
    else:
        doxyfile_content = doxyfile_template.generate_doxyfile(
            project_name=project_name,
            input_dirs=input_dirs,
            output_dir=str(output_dir),
            dot_path=dot_dir if have_dot else None,
            file_patterns=file_patterns,
            exclude_dirs=exclude_dirs,
            language=language,
            generate_html=not args.no_html,
            generate_xml=not args.no_xml,
            generate_graphs=have_dot,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        doxyfile_path = output_dir / "Doxyfile"
        doxyfile_path.write_text(doxyfile_content)
        if args.verbose:
            print(f"Doxyfile written to: {doxyfile_path}")

    # Clean old output directories so stale files don't persist
    for subdir in ("html", "xml"):
        old_dir = output_dir / subdir
        if old_dir.is_dir():
            if args.verbose:
                print(f"Removing old output: {old_dir}")
            shutil.rmtree(old_dir)

    # Run Doxygen
    env = plat_mod.get_env_for_subprocess()
    cmd = [str(doxygen_path), str(doxyfile_path)]

    if args.verbose:
        print(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(workspace),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        print("Error: Doxygen timed out after 600 seconds.", file=sys.stderr)
        save_cache(workspace, {**cache, "error": "timeout", "error_at": time.time()})
        sys.exit(1)

    if result.returncode != 0:
        stderr = result.stderr

        if detect_gatekeeper_error(stderr):
            print("Error: macOS Gatekeeper is blocking the binary.", file=sys.stderr)
            print(f"Run: xattr -cr {plat_mod.get_bin_dir()}", file=sys.stderr)
        else:
            print(f"Error: Doxygen failed (exit code {result.returncode}).", file=sys.stderr)
            if stderr:
                print("Doxygen stderr:", file=sys.stderr)
                print(stderr, file=sys.stderr)

        save_cache(workspace, {
            **cache,
            "error": stderr[:2000] if stderr else "unknown",
            "error_at": time.time(),
        })
        sys.exit(1)

    if result.stderr and args.verbose:
        print("Doxygen warnings:")
        print(result.stderr)

    # Validate output
    html_ok = (output_dir / "html" / "index.html").exists() if not args.no_html else True
    xml_ok = (output_dir / "xml" / "index.xml").exists() if not args.no_xml else True

    if not html_ok and not args.no_html:
        print("Warning: HTML output not found (html/index.html).", file=sys.stderr)
    if not xml_ok and not args.no_xml:
        print("Warning: XML output not found (xml/index.xml).", file=sys.stderr)

    # Update cache
    save_cache(workspace, {
        "generated_at": time.time(),
        "platform": plat,
        "output_dir": str(output_dir),
        "project_name": project_name,
        "language": language,
        "html": html_ok and not args.no_html,
        "xml": xml_ok and not args.no_xml,
        "graphs": have_dot,
    })

    # Summary
    print(f"Documentation generated successfully.")
    print(f"  Project:  {project_name}")
    print(f"  Language: {language}")
    if not args.no_html and html_ok:
        print(f"  HTML:     {output_dir}/html/index.html")
    if not args.no_xml and xml_ok:
        print(f"  XML:      {output_dir}/xml/index.xml")
    print(f"  Graphs:   {'enabled' if have_dot else 'disabled'}")


if __name__ == "__main__":
    main()
