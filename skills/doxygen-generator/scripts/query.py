#!/usr/bin/env python3
"""Query Doxygen XML output for symbols, call graphs, function bodies, and more.

Usage:
    python3 query.py <workspace> symbol <name>        [options]
    python3 query.py <workspace> callgraph <func>     [options]
    python3 query.py <workspace> body <func>           [options]
    python3 query.py <workspace> list                  [options]
    python3 query.py <workspace> search <pattern>      [options]

Examples:
    python3 query.py /path/to/project symbol main
    python3 query.py /path/to/project callgraph main --depth 3
    python3 query.py /path/to/project body process_data
    python3 query.py /path/to/project list --kind function
    python3 query.py /path/to/project search "init.*" --regex
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class SymbolInfo:
    """Parsed information about a Doxygen-documented symbol."""
    id: str
    name: str
    kind: str  # function, variable, typedef, enum, struct, class, define, file, ...
    file: str = ""
    line: int = 0
    body_start: int = 0
    body_end: int = 0
    return_type: str = ""
    params: str = ""
    brief: str = ""
    detailed: str = ""
    references: list[str] = field(default_factory=list)      # symbols this calls
    referenced_by: list[str] = field(default_factory=list)    # symbols that call this


class DoxygenXMLIndex:
    """Parses Doxygen XML output and provides query methods."""

    def __init__(self, xml_dir: Path):
        self.xml_dir = xml_dir
        self._index: dict[str, list[dict]] = {}   # name -> [{refid, kind, compound_refid}]
        self._compound_cache: dict[str, ET.Element] = {}
        self._all_symbols: list[SymbolInfo] | None = None
        self._parse_index()

    def _parse_index(self):
        """Parse index.xml to build name-to-refid mapping."""
        index_path = self.xml_dir / "index.xml"
        if not index_path.exists():
            raise FileNotFoundError(f"index.xml not found at: {index_path}")

        tree = ET.parse(str(index_path))
        root = tree.getroot()

        for compound in root.findall("compound"):
            compound_refid = compound.get("refid", "")
            compound_kind = compound.get("kind", "")
            compound_name = (compound.findtext("name") or "").strip()

            # Index the compound itself (file, class, struct, etc.)
            if compound_name:
                entry = {
                    "refid": compound_refid,
                    "kind": compound_kind,
                    "compound_refid": compound_refid,
                    "is_compound": True,
                }
                self._index.setdefault(compound_name, []).append(entry)

            # Index members (functions, variables, etc.)
            for member in compound.findall("member"):
                member_name = (member.findtext("name") or "").strip()
                member_refid = member.get("refid", "")
                member_kind = member.get("kind", "")
                if member_name:
                    entry = {
                        "refid": member_refid,
                        "kind": member_kind,
                        "compound_refid": compound_refid,
                        "is_compound": False,
                    }
                    self._index.setdefault(member_name, []).append(entry)

    def _load_compound(self, refid: str) -> Optional[ET.Element]:
        """Lazily load and cache a compound XML file."""
        if refid in self._compound_cache:
            return self._compound_cache[refid]

        xml_file = self.xml_dir / f"{refid}.xml"
        if not xml_file.exists():
            return None

        tree = ET.parse(str(xml_file))
        root = tree.getroot()
        self._compound_cache[refid] = root
        return root

    def _get_text(self, elem: Optional[ET.Element]) -> str:
        """Extract all text content from an element recursively."""
        if elem is None:
            return ""
        return "".join(elem.itertext()).strip()

    def _parse_memberdef(self, memberdef: ET.Element) -> SymbolInfo:
        """Parse a <memberdef> element into a SymbolInfo."""
        sym = SymbolInfo(
            id=memberdef.get("id", ""),
            name=self._get_text(memberdef.find("name")),
            kind=memberdef.get("kind", ""),
        )

        # Location
        location = memberdef.find("location")
        if location is not None:
            sym.file = location.get("file", "")
            sym.line = int(location.get("line", "0"))
            sym.body_start = int(location.get("bodystart", "0"))
            sym.body_end = int(location.get("bodyend", "0"))

        # Type/return type
        sym.return_type = self._get_text(memberdef.find("type"))

        # Parameters
        params = []
        for param in memberdef.findall("param"):
            ptype = self._get_text(param.find("type"))
            pname = self._get_text(param.find("declname"))
            if ptype or pname:
                params.append(f"{ptype} {pname}".strip())
        sym.params = ", ".join(params)

        # Brief description
        sym.brief = self._get_text(memberdef.find("briefdescription"))

        # Detailed description
        sym.detailed = self._get_text(memberdef.find("detaileddescription"))

        # References (what this function calls)
        for ref in memberdef.findall("references"):
            ref_name = self._get_text(ref)
            if ref_name:
                sym.references.append(ref_name)

        # Referenced by (what calls this function)
        for ref in memberdef.findall("referencedby"):
            ref_name = self._get_text(ref)
            if ref_name:
                sym.referenced_by.append(ref_name)

        return sym

    def find_symbol(self, name: str) -> list[SymbolInfo]:
        """Find all symbols matching the given name."""
        entries = self._index.get(name, [])
        results = []

        for entry in entries:
            if entry["is_compound"]:
                # For compounds (files, classes), create a basic SymbolInfo
                root = self._load_compound(entry["compound_refid"])
                if root is None:
                    continue
                compounddef = root.find(".//compounddef")
                if compounddef is None:
                    continue
                sym = SymbolInfo(
                    id=entry["refid"],
                    name=name,
                    kind=entry["kind"],
                )
                location = compounddef.find("location")
                if location is not None:
                    sym.file = location.get("file", "")
                    sym.line = int(location.get("line", "0"))
                sym.brief = self._get_text(compounddef.find("briefdescription"))
                results.append(sym)
            else:
                # For members, find the memberdef in the compound file
                root = self._load_compound(entry["compound_refid"])
                if root is None:
                    continue
                for memberdef in root.iter("memberdef"):
                    if memberdef.get("id") == entry["refid"]:
                        results.append(self._parse_memberdef(memberdef))
                        break

        return results

    def get_all_symbols(self) -> list[SymbolInfo]:
        """Get all symbols from the index (cached)."""
        if self._all_symbols is not None:
            return self._all_symbols

        symbols = []
        seen_ids = set()

        for name, entries in self._index.items():
            for entry in entries:
                if entry["is_compound"]:
                    continue  # Skip compounds for list, focus on members
                refid = entry["refid"]
                if refid in seen_ids:
                    continue
                seen_ids.add(refid)

                root = self._load_compound(entry["compound_refid"])
                if root is None:
                    continue
                for memberdef in root.iter("memberdef"):
                    if memberdef.get("id") == refid:
                        symbols.append(self._parse_memberdef(memberdef))
                        break

        self._all_symbols = symbols
        return symbols

    def build_callgraph(self, name: str, depth: int = 2,
                        direction: str = "both") -> dict:
        """Build a call graph for a function.

        Args:
            name: Function name to start from.
            depth: Maximum traversal depth.
            direction: 'calls' (outgoing), 'callers' (incoming), or 'both'.

        Returns:
            Nested dict: {name, kind, calls: [...], callers: [...]}
        """
        visited = set()

        def _traverse(fname: str, d: int, dir_: str) -> dict:
            node = {"name": fname, "calls": [], "callers": []}
            if d <= 0 or fname in visited:
                return node
            visited.add(fname)

            syms = self.find_symbol(fname)
            if not syms:
                return node

            sym = syms[0]
            node["kind"] = sym.kind
            node["file"] = sym.file
            node["line"] = sym.line

            if dir_ in ("calls", "both"):
                for ref in sym.references:
                    if ref not in visited:
                        child = _traverse(ref, d - 1, dir_)
                        node["calls"].append(child)
                    else:
                        node["calls"].append({"name": ref, "calls": [], "callers": [], "cycle": True})

            if dir_ in ("callers", "both"):
                for ref in sym.referenced_by:
                    if ref not in visited:
                        child = _traverse(ref, d - 1, dir_)
                        node["callers"].append(child)
                    else:
                        node["callers"].append({"name": ref, "calls": [], "callers": [], "cycle": True})

            return node

        return _traverse(name, depth, direction)


# --- Output Formatting ---

def format_symbol_text(sym: SymbolInfo) -> str:
    lines = [
        f"Name:         {sym.name}",
        f"Kind:         {sym.kind}",
        f"File:         {sym.file}",
        f"Line:         {sym.line}",
    ]
    if sym.return_type:
        lines.append(f"Return type:  {sym.return_type}")
    if sym.params:
        lines.append(f"Parameters:   {sym.params}")
    if sym.brief:
        lines.append(f"Brief:        {sym.brief}")
    if sym.body_start and sym.body_end:
        lines.append(f"Body:         lines {sym.body_start}-{sym.body_end}")
    if sym.references:
        lines.append(f"Calls:        {', '.join(sym.references)}")
    if sym.referenced_by:
        lines.append(f"Called by:     {', '.join(sym.referenced_by)}")
    return "\n".join(lines)


def format_callgraph_text(graph: dict, indent: int = 0, direction: str = "both") -> str:
    prefix = "  " * indent
    name = graph["name"]
    cycle = " (cycle)" if graph.get("cycle") else ""
    kind = graph.get("kind", "")
    kind_str = f" [{kind}]" if kind else ""

    lines = [f"{prefix}{name}{kind_str}{cycle}"]

    if direction in ("calls", "both") and graph.get("calls"):
        lines.append(f"{prefix}  Calls:")
        for child in graph["calls"]:
            lines.append(format_callgraph_text(child, indent + 2, "calls"))

    if direction in ("callers", "both") and graph.get("callers"):
        lines.append(f"{prefix}  Called by:")
        for child in graph["callers"]:
            lines.append(format_callgraph_text(child, indent + 2, "callers"))

    return "\n".join(lines)


def format_list_text(symbols: list[SymbolInfo]) -> str:
    if not symbols:
        return "No symbols found."

    # Find column widths
    max_name = max(len(s.name) for s in symbols)
    max_kind = max(len(s.kind) for s in symbols)
    max_file = max(len(os.path.basename(s.file)) for s in symbols) if symbols else 0

    header = f"{'Name':<{max_name}}  {'Kind':<{max_kind}}  {'File':<{max_file}}  Line"
    sep = "-" * len(header)
    lines = [header, sep]

    for s in symbols:
        fname = os.path.basename(s.file)
        lines.append(f"{s.name:<{max_name}}  {s.kind:<{max_kind}}  {fname:<{max_file}}  {s.line}")

    lines.append(f"\nTotal: {len(symbols)} symbols")
    return "\n".join(lines)


# --- Subcommands ---

def cmd_symbol(index: DoxygenXMLIndex, args) -> None:
    symbols = index.find_symbol(args.name)
    if not symbols:
        msg = f"Symbol not found: {args.name}"
        if args.format == "json":
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return

    if args.format == "json":
        print(json.dumps([asdict(s) for s in symbols], indent=2))
    else:
        for i, sym in enumerate(symbols):
            if i > 0:
                print("\n---\n")
            print(format_symbol_text(sym))


def cmd_callgraph(index: DoxygenXMLIndex, args) -> None:
    graph = index.build_callgraph(args.func, depth=args.depth, direction=args.direction)

    if args.format == "json":
        print(json.dumps(graph, indent=2))
    else:
        print(format_callgraph_text(graph, direction=args.direction))


def cmd_body(index: DoxygenXMLIndex, args) -> None:
    symbols = index.find_symbol(args.func)
    if not symbols:
        msg = f"Symbol not found: {args.func}"
        if args.format == "json":
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return

    sym = symbols[0]
    if not sym.body_start or not sym.body_end or not sym.file:
        msg = f"No body information available for: {args.func}"
        if args.format == "json":
            print(json.dumps({"error": msg, "symbol": asdict(sym)}))
        else:
            print(msg)
        return

    # Read source file
    source_path = Path(sym.file)
    if not source_path.is_absolute():
        source_path = Path(args.workspace) / source_path

    if not source_path.exists():
        msg = f"Source file not found: {source_path}"
        if args.format == "json":
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return

    try:
        all_lines = source_path.read_text().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        msg = f"Error reading source file: {e}"
        if args.format == "json":
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return

    start = max(0, sym.body_start - 1)  # Convert 1-based to 0-based
    end = min(len(all_lines), sym.body_end)
    body_lines = all_lines[start:end]

    if args.format == "json":
        print(json.dumps({
            "name": sym.name,
            "file": sym.file,
            "start_line": sym.body_start,
            "end_line": sym.body_end,
            "body": "\n".join(body_lines),
        }, indent=2))
    else:
        print(f"// {sym.file}:{sym.body_start}-{sym.body_end}")
        for i, line in enumerate(body_lines, start=sym.body_start):
            print(f"{i:>6}  {line}")


def cmd_list(index: DoxygenXMLIndex, args) -> None:
    symbols = index.get_all_symbols()

    # Filter by kind
    if args.kind:
        symbols = [s for s in symbols if s.kind == args.kind]

    # Filter by file
    if args.file:
        symbols = [s for s in symbols if args.file in s.file]

    # Sort
    symbols.sort(key=lambda s: (s.file, s.line))

    if args.format == "json":
        print(json.dumps([asdict(s) for s in symbols], indent=2))
    else:
        print(format_list_text(symbols))


def cmd_search(index: DoxygenXMLIndex, args) -> None:
    symbols = index.get_all_symbols()
    pattern = args.pattern

    if args.regex:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            msg = f"Invalid regex: {e}"
            if args.format == "json":
                print(json.dumps({"error": msg}))
            else:
                print(msg)
            return
        matches = [s for s in symbols if regex.search(s.name)]
    else:
        # Case-insensitive substring search, with basic glob support
        pat_lower = pattern.lower()
        if "*" in pat_lower or "?" in pat_lower:
            # Convert glob to regex
            regex_str = pat_lower.replace(".", r"\.").replace("*", ".*").replace("?", ".")
            try:
                regex = re.compile(f"^{regex_str}$", re.IGNORECASE)
            except re.error:
                regex = None
            if regex:
                matches = [s for s in symbols if regex.search(s.name)]
            else:
                matches = [s for s in symbols if pat_lower in s.name.lower()]
        else:
            matches = [s for s in symbols if pat_lower in s.name.lower()]

    matches.sort(key=lambda s: (s.file, s.line))

    if args.format == "json":
        print(json.dumps([asdict(s) for s in matches], indent=2))
    else:
        if matches:
            print(format_list_text(matches))
        else:
            print(f"No symbols matching: {pattern}")


# --- Main ---

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Query Doxygen XML documentation."
    )
    parser.add_argument("workspace", help="Path to the workspace/project root.")
    parser.add_argument("--output-dir", default=".doxygen",
                        help="Doxygen output directory relative to workspace (default: .doxygen).")
    parser.add_argument("--xml-dir", default=None,
                        help="Override XML directory path (default: <output-dir>/xml).")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format (default: text).")
    parser.add_argument("-v", "--verbose", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # symbol
    p_symbol = subparsers.add_parser("symbol", help="Look up a symbol by name.")
    p_symbol.add_argument("name", help="Symbol name to look up.")

    # callgraph
    p_cg = subparsers.add_parser("callgraph", help="Show call graph for a function.")
    p_cg.add_argument("func", help="Function name.")
    p_cg.add_argument("--depth", type=int, default=2,
                      help="Max traversal depth (default: 2).")
    p_cg.add_argument("--direction", choices=["calls", "callers", "both"],
                      default="both", help="Graph direction (default: both).")

    # body
    p_body = subparsers.add_parser("body", help="Extract function source code.")
    p_body.add_argument("func", help="Function name.")

    # list
    p_list = subparsers.add_parser("list", help="List all documented symbols.")
    p_list.add_argument("--kind", default=None,
                        help="Filter by kind (e.g., function, variable, typedef).")
    p_list.add_argument("--file", default=None,
                        help="Filter by file path (substring match).")

    # search
    p_search = subparsers.add_parser("search", help="Search symbols by pattern.")
    p_search.add_argument("pattern", help="Search pattern.")
    p_search.add_argument("--regex", action="store_true",
                          help="Treat pattern as regex.")

    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    workspace = Path(args.workspace).resolve()

    if not workspace.is_dir():
        print(f"Error: Workspace not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    # Resolve XML directory
    if args.xml_dir:
        xml_dir = Path(args.xml_dir).resolve()
    else:
        xml_dir = workspace / args.output_dir / "xml"

    if not xml_dir.exists():
        print(f"Error: XML directory not found: {xml_dir}", file=sys.stderr)
        print("Run generate.py first to create documentation.", file=sys.stderr)
        sys.exit(1)

    try:
        index = DoxygenXMLIndex(xml_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.command == "symbol":
        cmd_symbol(index, args)
    elif args.command == "callgraph":
        cmd_callgraph(index, args)
    elif args.command == "body":
        cmd_body(index, args)
    elif args.command == "list":
        cmd_list(index, args)
    elif args.command == "search":
        cmd_search(index, args)


if __name__ == "__main__":
    main()
