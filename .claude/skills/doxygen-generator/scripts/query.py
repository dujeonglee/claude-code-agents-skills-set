#!/usr/bin/env python3
"""Query Doxygen XML output for symbols, call graphs, function bodies, and more.

Usage:
    python3 query.py <workspace> symbol <name>        [options]
    python3 query.py <workspace> callgraph <func>     [options]
    python3 query.py <workspace> body <func>           [options]
    python3 query.py <workspace> list                  [options]
    python3 query.py <workspace> search <pattern>      [options]
    python3 query.py <workspace> stats                 [options]
    python3 query.py <workspace> members <compound>    [options]
    python3 query.py <workspace> files                 [options]
    python3 query.py <workspace> file <path>           [options]

Examples:
    python3 query.py /path/to/project symbol main
    python3 query.py /path/to/project callgraph main --depth 3
    python3 query.py /path/to/project body process_data
    python3 query.py /path/to/project list --kind function --limit 20
    python3 query.py /path/to/project search "init.*" --regex --count
    python3 query.py /path/to/project stats
    python3 query.py /path/to/project members my_struct
    python3 query.py /path/to/project files
    python3 query.py /path/to/project file src/main.c
"""

import argparse
import difflib
import json
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterator, Optional


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


# --- Utility helpers ---

def symbol_to_dict(sym: SymbolInfo, compact: bool = False) -> dict:
    """Convert SymbolInfo to dict, optionally stripping empty/zero fields."""
    d = asdict(sym)
    if compact:
        return {k: v for k, v in d.items() if v}
    return d


def _paginate(items: list, limit: int, offset: int) -> tuple[list, dict]:
    """Paginate a list. Returns (page, meta) with truncation info."""
    total = len(items)
    page = items[offset:offset + limit] if limit > 0 else items[offset:]
    truncated = (offset + len(page)) < total
    meta = {
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": truncated,
    }
    if truncated:
        next_offset = offset + limit
        remaining = total - next_offset
        meta["hint"] = (
            f"Use --offset {next_offset} to see next "
            f"{min(remaining, limit)} of {remaining} remaining results."
        )
    return page, meta


def _did_you_mean(index, name: str, n: int = 5) -> list[str]:
    """Return fuzzy matches for a symbol name."""
    all_names = index.get_all_names()
    return difflib.get_close_matches(name, all_names, n=n, cutoff=0.6)


# --- Standalone XML helpers (usable by both in-memory and SQLite indexes) ---

def _get_text(elem: Optional[ET.Element]) -> str:
    """Extract all text content from an element recursively."""
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def _parse_memberdef_element(memberdef: ET.Element) -> SymbolInfo:
    """Parse a <memberdef> element into a SymbolInfo (standalone)."""
    sym = SymbolInfo(
        id=memberdef.get("id", ""),
        name=_get_text(memberdef.find("name")),
        kind=memberdef.get("kind", ""),
    )

    location = memberdef.find("location")
    if location is not None:
        sym.file = location.get("file", "")
        sym.line = int(location.get("line", "0"))
        sym.body_start = int(location.get("bodystart", "0"))
        sym.body_end = int(location.get("bodyend", "0"))

    sym.return_type = _get_text(memberdef.find("type"))

    params = []
    for param in memberdef.findall("param"):
        ptype = _get_text(param.find("type"))
        pname = _get_text(param.find("declname"))
        if ptype or pname:
            params.append(f"{ptype} {pname}".strip())
    sym.params = ", ".join(params)

    sym.brief = _get_text(memberdef.find("briefdescription"))
    sym.detailed = _get_text(memberdef.find("detaileddescription"))

    for ref in memberdef.findall("references"):
        ref_name = _get_text(ref)
        if ref_name:
            sym.references.append(ref_name)

    for ref in memberdef.findall("referencedby"):
        ref_name = _get_text(ref)
        if ref_name:
            sym.referenced_by.append(ref_name)

    return sym


def _iterparse_compound(xml_path: Path) -> Iterator[SymbolInfo]:
    """Stream-parse a Doxygen compound XML file, yielding SymbolInfo per memberdef.

    Uses ET.iterparse with elem.clear() to avoid holding full trees in memory.
    """
    try:
        for event, elem in ET.iterparse(str(xml_path), events=("end",)):
            if elem.tag == "memberdef":
                yield _parse_memberdef_element(elem)
                elem.clear()
            elif elem.tag == "compounddef":
                # Clear the compound to free accumulated sub-elements
                elem.clear()
    except ET.ParseError:
        return


def _parse_compound_members(xml_dir: Path, compound_refid: str) -> Optional[list[dict]]:
    """Parse memberdef elements from a compound XML file.

    Returns list of member dicts, or None if file not found.
    """
    xml_path = xml_dir / f"{compound_refid}.xml"
    if not xml_path.exists():
        return None

    try:
        tree = ET.parse(str(xml_path))
    except ET.ParseError:
        return None

    root = tree.getroot()
    compounddef = root.find(".//compounddef")
    if compounddef is None:
        return None

    members = []
    for memberdef in compounddef.iter("memberdef"):
        member = {
            "name": _get_text(memberdef.find("name")),
            "kind": memberdef.get("kind", ""),
            "type": _get_text(memberdef.find("type")),
            "brief": _get_text(memberdef.find("briefdescription")),
        }
        loc = memberdef.find("location")
        if loc is not None:
            member["line"] = int(loc.get("line", "0"))
        members.append(member)

    return members


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

    def _parse_memberdef(self, memberdef: ET.Element) -> SymbolInfo:
        """Parse a <memberdef> element into a SymbolInfo."""
        return _parse_memberdef_element(memberdef)

    def find_symbol(self, name: str, scope: str = "") -> list[SymbolInfo]:
        """Find all symbols matching the given name.

        Args:
            name: Symbol name to look up.
            scope: If non-empty, only return symbols whose file starts with this prefix.
        """
        entries = self._index.get(name, [])
        results = []

        for entry in entries:
            if entry["is_compound"]:
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
                sym.brief = _get_text(compounddef.find("briefdescription"))
                if scope and not sym.file.startswith(scope):
                    continue
                results.append(sym)
            else:
                root = self._load_compound(entry["compound_refid"])
                if root is None:
                    continue
                for memberdef in root.iter("memberdef"):
                    if memberdef.get("id") == entry["refid"]:
                        sym = self._parse_memberdef(memberdef)
                        if scope and not sym.file.startswith(scope):
                            break
                        results.append(sym)
                        break

        return results

    def get_all_symbols(self, scope: str = "") -> list[SymbolInfo]:
        """Get all symbols from the index.

        Uses streaming iterparse for memory efficiency on large codebases.
        Results are cached (only the unscoped full set).

        Args:
            scope: If non-empty, only return symbols whose file starts with this prefix.
        """
        if self._all_symbols is not None:
            syms = self._all_symbols
            if scope:
                return [s for s in syms if s.file.startswith(scope)]
            return syms

        symbols = []
        seen_ids: set[str] = set()

        # Collect compound refids that have members
        compound_refids: set[str] = set()
        for entries in self._index.values():
            for entry in entries:
                if not entry["is_compound"]:
                    compound_refids.add(entry["compound_refid"])

        # Stream-parse each compound file once
        for refid in compound_refids:
            xml_path = self.xml_dir / f"{refid}.xml"
            if not xml_path.exists():
                continue
            for sym in _iterparse_compound(xml_path):
                if sym.id and sym.id not in seen_ids:
                    seen_ids.add(sym.id)
                    symbols.append(sym)

        self._all_symbols = symbols
        if scope:
            return [s for s in symbols if s.file.startswith(scope)]
        return symbols

    def get_all_names(self) -> list[str]:
        """Return all symbol names in the index."""
        return list(self._index.keys())

    def get_all_kinds(self) -> list[str]:
        """Return all distinct symbol kinds."""
        kinds: set[str] = set()
        for entries in self._index.values():
            for entry in entries:
                kinds.add(entry["kind"])
        return sorted(kinds)

    def get_stats(self) -> dict:
        """Return summary statistics about the index."""
        symbols = self.get_all_symbols()
        by_kind: dict[str, int] = {}
        by_file: dict[str, int] = {}
        for s in symbols:
            by_kind[s.kind] = by_kind.get(s.kind, 0) + 1
            if s.file:
                by_file[s.file] = by_file.get(s.file, 0) + 1
        return {
            "total_symbols": len(symbols),
            "by_kind": dict(sorted(by_kind.items(), key=lambda x: -x[1])),
            "by_file": dict(sorted(by_file.items(), key=lambda x: -x[1])),
            "index_backend": "xml",
            "xml_files": len(list(self.xml_dir.glob("*.xml"))),
        }

    def get_members(self, compound_name: str) -> Optional[dict]:
        """Get members of a compound (struct/class/union).

        Returns dict with compound info and member list, or None if not found.
        """
        entries = self._index.get(compound_name, [])
        compound_entries = [e for e in entries if e["is_compound"]]
        if not compound_entries:
            return None

        entry = compound_entries[0]
        root = self._load_compound(entry["compound_refid"])
        if root is None:
            return None

        compounddef = root.find(".//compounddef")
        if compounddef is None:
            return None

        result = {
            "name": compound_name,
            "kind": entry["kind"],
            "file": "",
            "line": 0,
            "brief": _get_text(compounddef.find("briefdescription")),
            "members": [],
        }

        location = compounddef.find("location")
        if location is not None:
            result["file"] = location.get("file", "")
            result["line"] = int(location.get("line", "0"))

        for memberdef in compounddef.iter("memberdef"):
            member = {
                "name": _get_text(memberdef.find("name")),
                "kind": memberdef.get("kind", ""),
                "type": _get_text(memberdef.find("type")),
                "brief": _get_text(memberdef.find("briefdescription")),
            }
            loc = memberdef.find("location")
            if loc is not None:
                member["line"] = int(loc.get("line", "0"))
            result["members"].append(member)

        return result

    def get_all_files(self, scope: str = "") -> list[str]:
        """Return all distinct file paths from symbols."""
        files: set[str] = set()
        for s in self.get_all_symbols():
            if s.file:
                if scope and not s.file.startswith(scope):
                    continue
                files.add(s.file)
        return sorted(files)

    def get_symbols_in_file(self, file_path: str) -> list[SymbolInfo]:
        """Return all symbols defined in a specific file, sorted by line."""
        results = [s for s in self.get_all_symbols() if s.file == file_path]
        results.sort(key=lambda s: s.line)
        return results

    def build_callgraph(self, name: str, depth: int = 2,
                        direction: str = "both",
                        max_nodes: int = 0,
                        exclude_kinds: set[str] | None = None) -> dict:
        """Build a call graph for a function.

        Args:
            name: Function name to start from.
            depth: Maximum traversal depth.
            direction: 'calls' (outgoing), 'callers' (incoming), or 'both'.
            max_nodes: Maximum number of nodes (0 = unlimited).
            exclude_kinds: Symbol kinds to exclude from the graph.

        Returns:
            Nested dict with _meta containing truncation info.
        """
        visited = set()
        node_count = [0]

        def _traverse(fname: str, d: int, dir_: str) -> dict:
            node = {"name": fname, "calls": [], "callers": []}
            if d <= 0 or fname in visited:
                return node
            if max_nodes > 0 and node_count[0] >= max_nodes:
                return node
            visited.add(fname)
            node_count[0] += 1

            syms = self.find_symbol(fname)
            if not syms:
                return node

            sym = syms[0]
            if exclude_kinds and sym.kind in exclude_kinds:
                return node

            node["kind"] = sym.kind
            node["file"] = sym.file
            node["line"] = sym.line

            if dir_ in ("calls", "both"):
                for ref in sym.references:
                    if max_nodes > 0 and node_count[0] >= max_nodes:
                        break
                    if ref not in visited:
                        node["calls"].append(_traverse(ref, d - 1, dir_))
                    else:
                        node["calls"].append({"name": ref, "calls": [], "callers": [], "cycle": True})

            if dir_ in ("callers", "both"):
                for ref in sym.referenced_by:
                    if max_nodes > 0 and node_count[0] >= max_nodes:
                        break
                    if ref not in visited:
                        node["callers"].append(_traverse(ref, d - 1, dir_))
                    else:
                        node["callers"].append({"name": ref, "calls": [], "callers": [], "cycle": True})

            return node

        result = _traverse(name, depth, direction)
        truncated = max_nodes > 0 and node_count[0] >= max_nodes
        result["_meta"] = {
            "total_nodes": node_count[0],
            "max_nodes": max_nodes,
            "truncated": truncated,
        }
        if truncated:
            result["_meta"]["hint"] = (
                f"Graph truncated at {max_nodes} nodes. "
                f"Use --max-nodes {max_nodes * 2} to see more."
            )
        return result


class DoxygenSQLiteIndex:
    """SQLite-backed symbol index — same public API as DoxygenXMLIndex.

    Builds a SQLite database from Doxygen XML on first query.  Subsequent
    queries skip XML parsing if the DB is still fresh (XML mtimes haven't
    changed).  The database is stored alongside the XML output at
    ``<output_dir>/symbols.db``.
    """

    def __init__(self, xml_dir: Path, db_path: Optional[Path] = None):
        self.xml_dir = xml_dir
        self.db_path = db_path or xml_dir.parent / "symbols.db"
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_db()

    # -- lifecycle ----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_db(self) -> None:
        """Create or refresh the database if needed."""
        if self.db_path.exists() and not self._is_stale():
            return
        self._build_db()

    def _is_stale(self) -> bool:
        """Compare DB mtime against the newest XML file mtime."""
        try:
            db_mtime = self.db_path.stat().st_mtime
        except OSError:
            return True
        for xml_file in self.xml_dir.glob("*.xml"):
            try:
                if xml_file.stat().st_mtime > db_mtime:
                    return True
            except OSError:
                continue
        return False

    def _build_db(self) -> None:
        """Parse all XML files and populate the SQLite database."""
        conn = self._connect()
        conn.executescript("""
            DROP TABLE IF EXISTS symbols;
            DROP TABLE IF EXISTS refs;
            DROP TABLE IF EXISTS meta;

            CREATE TABLE symbols (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                kind       TEXT NOT NULL,
                file       TEXT,
                line       INTEGER,
                body_start INTEGER,
                body_end   INTEGER,
                return_type TEXT,
                params     TEXT,
                brief      TEXT,
                detailed   TEXT,
                is_compound INTEGER DEFAULT 0
            );

            CREATE TABLE refs (
                from_id   TEXT NOT NULL,
                to_name   TEXT NOT NULL,
                direction TEXT NOT NULL  -- 'calls' or 'callers'
            );

            CREATE TABLE meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE INDEX idx_symbols_name ON symbols(name);
            CREATE INDEX idx_symbols_kind ON symbols(kind);
            CREATE INDEX idx_symbols_file ON symbols(file);
            CREATE INDEX idx_refs_from    ON refs(from_id);
            CREATE INDEX idx_refs_to      ON refs(to_name);
        """)

        # Parse index.xml for compounds
        index_path = self.xml_dir / "index.xml"
        if not index_path.exists():
            raise FileNotFoundError(f"index.xml not found at: {index_path}")

        tree = ET.parse(str(index_path))
        root = tree.getroot()

        # Insert compound-level symbols and collect refids
        compound_refids: set[str] = set()
        compound_kinds: dict[str, str] = {}  # refid -> kind
        for compound in root.findall("compound"):
            crefid = compound.get("refid", "")
            ckind = compound.get("kind", "")
            cname = (compound.findtext("name") or "").strip()
            if cname:
                conn.execute(
                    "INSERT OR IGNORE INTO symbols (id, name, kind, is_compound) VALUES (?,?,?,1)",
                    (crefid, cname, ckind),
                )
            compound_kinds[crefid] = ckind
            # Check if this compound has members
            if compound.findall("member"):
                compound_refids.add(crefid)

        # Stream-parse each compound XML for member symbols
        for refid in compound_refids:
            xml_path = self.xml_dir / f"{refid}.xml"
            if not xml_path.exists():
                continue
            for sym in _iterparse_compound(xml_path):
                conn.execute(
                    """INSERT OR IGNORE INTO symbols
                       (id, name, kind, file, line, body_start, body_end,
                        return_type, params, brief, detailed, is_compound)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
                    (sym.id, sym.name, sym.kind, sym.file, sym.line,
                     sym.body_start, sym.body_end, sym.return_type,
                     sym.params, sym.brief, sym.detailed),
                )
                for ref_name in sym.references:
                    conn.execute(
                        "INSERT INTO refs (from_id, to_name, direction) VALUES (?,?,?)",
                        (sym.id, ref_name, "calls"),
                    )
                for ref_name in sym.referenced_by:
                    conn.execute(
                        "INSERT INTO refs (from_id, to_name, direction) VALUES (?,?,?)",
                        (sym.id, ref_name, "callers"),
                    )

        # Item 12: Populate compound locations (struct/class/union/namespace)
        compound_loc_kinds = {"struct", "class", "union", "namespace"}
        for crefid, ckind in compound_kinds.items():
            if ckind not in compound_loc_kinds:
                continue
            xml_path = self.xml_dir / f"{crefid}.xml"
            if not xml_path.exists():
                continue
            try:
                ctree = ET.parse(str(xml_path))
                cdef = ctree.getroot().find(".//compounddef")
                if cdef is not None:
                    loc = cdef.find("location")
                    if loc is not None:
                        cfile = loc.get("file", "")
                        cline = int(loc.get("line", "0"))
                        if cfile:
                            conn.execute(
                                "UPDATE symbols SET file=?, line=? WHERE id=?",
                                (cfile, cline, crefid),
                            )
            except ET.ParseError:
                continue

        import time as _time
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('built_at', ?)",
            (str(_time.time()),),
        )
        conn.commit()

    # -- public API (mirrors DoxygenXMLIndex) --------------------------------

    def _row_to_symbol(self, row: sqlite3.Row) -> SymbolInfo:
        sym = SymbolInfo(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            file=row["file"] or "",
            line=row["line"] or 0,
            body_start=row["body_start"] or 0,
            body_end=row["body_end"] or 0,
            return_type=row["return_type"] or "",
            params=row["params"] or "",
            brief=row["brief"] or "",
            detailed=row["detailed"] or "",
        )
        conn = self._connect()
        for r in conn.execute(
            "SELECT to_name FROM refs WHERE from_id=? AND direction='calls'",
            (sym.id,),
        ):
            sym.references.append(r["to_name"])
        for r in conn.execute(
            "SELECT to_name FROM refs WHERE from_id=? AND direction='callers'",
            (sym.id,),
        ):
            sym.referenced_by.append(r["to_name"])
        return sym

    def find_symbol(self, name: str, scope: str = "") -> list[SymbolInfo]:
        conn = self._connect()
        if scope:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE name=? AND (file LIKE ? OR file IS NULL)",
                (name, scope + "%"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE name=?", (name,)
            ).fetchall()
        return [self._row_to_symbol(r) for r in rows]

    def get_all_symbols(self, scope: str = "") -> list[SymbolInfo]:
        conn = self._connect()
        if scope:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE is_compound=0 AND file LIKE ?",
                (scope + "%",),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE is_compound=0"
            ).fetchall()
        return [self._row_to_symbol(r) for r in rows]

    def get_all_names(self) -> list[str]:
        """Return all distinct symbol names."""
        conn = self._connect()
        rows = conn.execute("SELECT DISTINCT name FROM symbols").fetchall()
        return [r["name"] for r in rows]

    def get_all_kinds(self) -> list[str]:
        """Return all distinct symbol kinds."""
        conn = self._connect()
        rows = conn.execute("SELECT DISTINCT kind FROM symbols").fetchall()
        return sorted(r["kind"] for r in rows)

    def get_stats(self) -> dict:
        """Return summary statistics about the index."""
        conn = self._connect()
        total = conn.execute(
            "SELECT COUNT(*) as c FROM symbols WHERE is_compound=0"
        ).fetchone()["c"]
        by_kind = {}
        for r in conn.execute(
            "SELECT kind, COUNT(*) as c FROM symbols WHERE is_compound=0 "
            "GROUP BY kind ORDER BY c DESC"
        ):
            by_kind[r["kind"]] = r["c"]
        by_file = {}
        for r in conn.execute(
            "SELECT file, COUNT(*) as c FROM symbols WHERE is_compound=0 "
            "AND file != '' GROUP BY file ORDER BY c DESC"
        ):
            by_file[r["file"]] = r["c"]

        db_size = 0
        try:
            db_size = self.db_path.stat().st_size
        except OSError:
            pass

        return {
            "total_symbols": total,
            "by_kind": by_kind,
            "by_file": by_file,
            "index_backend": "sqlite",
            "db_size_bytes": db_size,
            "xml_files": len(list(self.xml_dir.glob("*.xml"))),
        }

    def get_members(self, compound_name: str) -> Optional[dict]:
        """Get members of a compound (struct/class/union)."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM symbols WHERE name=? AND is_compound=1",
            (compound_name,),
        ).fetchone()
        if row is None:
            return None

        result = {
            "name": compound_name,
            "kind": row["kind"],
            "file": row["file"] or "",
            "line": row["line"] or 0,
            "brief": row["brief"] or "",
            "members": [],
        }

        # Parse compound XML for member details
        members = _parse_compound_members(self.xml_dir, row["id"])
        if members is not None:
            result["members"] = members

        return result

    def get_all_files(self, scope: str = "") -> list[str]:
        """Return all distinct file paths."""
        conn = self._connect()
        if scope:
            rows = conn.execute(
                "SELECT DISTINCT file FROM symbols WHERE file != '' "
                "AND file LIKE ? ORDER BY file",
                (scope + "%",),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT file FROM symbols WHERE file != '' ORDER BY file"
            ).fetchall()
        return [r["file"] for r in rows]

    def get_symbols_in_file(self, file_path: str) -> list[SymbolInfo]:
        """Return all symbols defined in a specific file, sorted by line."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM symbols WHERE file=? AND is_compound=0 ORDER BY line",
            (file_path,),
        ).fetchall()
        return [self._row_to_symbol(r) for r in rows]

    def build_callgraph(self, name: str, depth: int = 2,
                        direction: str = "both",
                        max_nodes: int = 0,
                        exclude_kinds: set[str] | None = None) -> dict:
        visited: set[str] = set()
        node_count = [0]

        def _traverse(fname: str, d: int, dir_: str) -> dict:
            node: dict = {"name": fname, "calls": [], "callers": []}
            if d <= 0 or fname in visited:
                return node
            if max_nodes > 0 and node_count[0] >= max_nodes:
                return node
            visited.add(fname)
            node_count[0] += 1

            syms = self.find_symbol(fname)
            if not syms:
                return node

            sym = syms[0]
            if exclude_kinds and sym.kind in exclude_kinds:
                return node

            node["kind"] = sym.kind
            node["file"] = sym.file
            node["line"] = sym.line

            if dir_ in ("calls", "both"):
                for ref in sym.references:
                    if max_nodes > 0 and node_count[0] >= max_nodes:
                        break
                    if ref not in visited:
                        node["calls"].append(_traverse(ref, d - 1, dir_))
                    else:
                        node["calls"].append({"name": ref, "calls": [], "callers": [], "cycle": True})

            if dir_ in ("callers", "both"):
                for ref in sym.referenced_by:
                    if max_nodes > 0 and node_count[0] >= max_nodes:
                        break
                    if ref not in visited:
                        node["callers"].append(_traverse(ref, d - 1, dir_))
                    else:
                        node["callers"].append({"name": ref, "calls": [], "callers": [], "cycle": True})

            return node

        result = _traverse(name, depth, direction)
        truncated = max_nodes > 0 and node_count[0] >= max_nodes
        result["_meta"] = {
            "total_nodes": node_count[0],
            "max_nodes": max_nodes,
            "truncated": truncated,
        }
        if truncated:
            result["_meta"]["hint"] = (
                f"Graph truncated at {max_nodes} nodes. "
                f"Use --max-nodes {max_nodes * 2} to see more."
            )
        return result


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

def cmd_symbol(index, args) -> int:
    scope = getattr(args, "scope", "")
    compact = getattr(args, "compact", False)
    symbols = index.find_symbol(args.name, scope=scope)
    if not symbols:
        error = {"error": f"Symbol not found: {args.name}"}
        suggestions = _did_you_mean(index, args.name)
        if suggestions:
            error["did_you_mean"] = suggestions
        if args.format == "json":
            print(json.dumps(error))
        else:
            print(error["error"])
            if suggestions:
                print(f"Did you mean: {', '.join(suggestions)}")
        return 1

    if args.format == "json":
        limit = getattr(args, "limit", 50)
        offset = getattr(args, "offset", 0)
        page, meta = _paginate(symbols, limit, offset)
        output = {**meta, "results": [symbol_to_dict(s, compact) for s in page]}
        print(json.dumps(output, indent=2))
    else:
        for i, sym in enumerate(symbols):
            if i > 0:
                print("\n---\n")
            print(format_symbol_text(sym))
    return 0


def cmd_callgraph(index, args) -> int:
    max_nodes = getattr(args, "max_nodes", 200)
    include_macros = getattr(args, "include_macros", False)

    # Build exclude_kinds set
    if include_macros:
        exclude_kinds = set()
    else:
        exclude_kinds = {"define"}

    # Add any explicit --exclude-kinds
    extra_excludes = getattr(args, "exclude_kinds", None)
    if extra_excludes:
        exclude_kinds.update(extra_excludes)

    exclude_kinds = exclude_kinds or None  # Convert empty set to None

    graph = index.build_callgraph(
        args.func, depth=args.depth, direction=args.direction,
        max_nodes=max_nodes, exclude_kinds=exclude_kinds,
    )

    if args.format == "json":
        print(json.dumps(graph, indent=2))
    else:
        print(format_callgraph_text(graph, direction=args.direction))
        meta = graph.get("_meta", {})
        if meta.get("truncated"):
            print(f"\n[Truncated: {meta['total_nodes']} nodes shown, "
                  f"limit {meta['max_nodes']}. {meta.get('hint', '')}]")
    return 0


def cmd_body(index, args) -> int:
    symbols = index.find_symbol(args.func)
    if not symbols:
        error = {"error": f"Symbol not found: {args.func}"}
        suggestions = _did_you_mean(index, args.func)
        if suggestions:
            error["did_you_mean"] = suggestions
        if args.format == "json":
            print(json.dumps(error))
        else:
            print(error["error"])
            if suggestions:
                print(f"Did you mean: {', '.join(suggestions)}")
        return 1

    sym = symbols[0]
    if not sym.body_start or not sym.body_end or not sym.file:
        msg = f"No body information available for: {args.func}"
        if args.format == "json":
            print(json.dumps({"error": msg, "symbol": symbol_to_dict(sym, getattr(args, "compact", False))}))
        else:
            print(msg)
        return 1

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
        return 1

    try:
        all_lines = source_path.read_text().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        msg = f"Error reading source file: {e}"
        if args.format == "json":
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return 1

    # Use the earlier of sym.line (declaration) and sym.body_start so that
    # multiline signatures are included in the extracted body.
    actual_start = min(sym.line, sym.body_start) if sym.line > 0 else sym.body_start
    start = max(0, actual_start - 1)  # Convert 1-based to 0-based
    end = min(len(all_lines), sym.body_end)
    body_lines = all_lines[start:end]

    if args.format == "json":
        print(json.dumps({
            "name": sym.name,
            "file": sym.file,
            "start_line": actual_start,
            "end_line": sym.body_end,
            "body": "\n".join(body_lines),
        }, indent=2))
    else:
        print(f"// {sym.file}:{actual_start}-{sym.body_end}")
        for i, line in enumerate(body_lines, start=actual_start):
            print(f"{i:>6}  {line}")
    return 0


def cmd_list(index, args) -> int:
    scope = getattr(args, "scope", "")
    compact = getattr(args, "compact", False)
    count_only = getattr(args, "count", False)
    kind = getattr(args, "kind", None)
    file_filter = getattr(args, "file", None)

    # Validate --kind
    if kind:
        valid_kinds = index.get_all_kinds()
        if kind not in valid_kinds:
            result = {
                "error": f"Unknown kind: {kind}",
                "valid_kinds": valid_kinds,
                "hint": f"Use one of: {', '.join(valid_kinds)}",
            }
            if args.format == "json":
                print(json.dumps(result))
            else:
                print(result["error"])
                print(result["hint"])
            return 1

    symbols = index.get_all_symbols(scope=scope)

    # Filter by kind
    if kind:
        symbols = [s for s in symbols if s.kind == kind]

    # Filter by file
    if file_filter:
        symbols = [s for s in symbols if file_filter in s.file]

    # Sort
    symbols.sort(key=lambda s: (s.file, s.line))

    if count_only:
        if args.format == "json":
            print(json.dumps({"count": len(symbols)}))
        else:
            print(f"Count: {len(symbols)}")
        return 0

    if args.format == "json":
        limit = getattr(args, "limit", 50)
        offset = getattr(args, "offset", 0)
        page, meta = _paginate(symbols, limit, offset)
        output = {**meta, "results": [symbol_to_dict(s, compact) for s in page]}
        print(json.dumps(output, indent=2))
    else:
        print(format_list_text(symbols))
    return 0


def cmd_search(index, args) -> int:
    scope = getattr(args, "scope", "")
    compact = getattr(args, "compact", False)
    count_only = getattr(args, "count", False)
    symbols = index.get_all_symbols(scope=scope)
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
            return 1
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

    if count_only:
        if args.format == "json":
            print(json.dumps({"count": len(matches)}))
        else:
            print(f"Count: {len(matches)}")
        return 0

    if args.format == "json":
        limit = getattr(args, "limit", 50)
        offset = getattr(args, "offset", 0)
        page, meta = _paginate(matches, limit, offset)
        output = {**meta, "results": [symbol_to_dict(s, compact) for s in page]}
        print(json.dumps(output, indent=2))
    else:
        if matches:
            print(format_list_text(matches))
        else:
            print(f"No symbols matching: {pattern}")
    return 0


def cmd_stats(index, args) -> int:
    stats = index.get_stats()

    # Cap by_file at top 20
    by_file = stats["by_file"]
    by_file_total = len(by_file)
    if by_file_total > 20:
        top_files = dict(list(by_file.items())[:20])
        stats["by_file"] = top_files
        stats["by_file_truncated"] = True
        stats["by_file_total"] = by_file_total
        stats["by_file_hint"] = (
            f"Showing top 20 of {by_file_total} files. "
            f"Use 'files' subcommand for the full list."
        )

    if args.format == "json":
        print(json.dumps(stats, indent=2))
    else:
        print(f"Total symbols: {stats['total_symbols']}")
        print(f"Backend: {stats.get('index_backend', 'unknown')}")
        print(f"\nBy kind:")
        for kind, count in stats["by_kind"].items():
            print(f"  {kind}: {count}")
        print(f"\nBy file (top {min(20, by_file_total)}):")
        for fpath, count in stats["by_file"].items():
            print(f"  {os.path.basename(fpath)}: {count}")
        if stats.get("by_file_truncated"):
            print(f"  ... and {by_file_total - 20} more files")
    return 0


def cmd_members(index, args) -> int:
    compact = getattr(args, "compact", False)
    result = index.get_members(args.compound)
    if result is None:
        error = {"error": f"Compound not found: {args.compound}"}
        suggestions = _did_you_mean(index, args.compound)
        if suggestions:
            error["did_you_mean"] = suggestions
        if args.format == "json":
            print(json.dumps(error))
        else:
            print(error["error"])
            if suggestions:
                print(f"Did you mean: {', '.join(suggestions)}")
        return 1

    if args.format == "json":
        limit = getattr(args, "limit", 50)
        offset = getattr(args, "offset", 0)
        members = result["members"]
        page, meta = _paginate(members, limit, offset)
        output = {
            "name": result["name"],
            "kind": result["kind"],
            "file": result["file"],
            "line": result["line"],
            "brief": result["brief"],
            **meta,
            "members": page if not compact else [
                {k: v for k, v in m.items() if v and v != 0} for m in page
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"Compound: {result['name']} ({result['kind']})")
        print(f"File:     {result['file']}:{result['line']}")
        if result["brief"]:
            print(f"Brief:    {result['brief']}")
        print(f"\nMembers ({len(result['members'])}):")
        for m in result["members"]:
            line_str = f":{m['line']}" if m.get("line") else ""
            print(f"  {m['type']} {m['name']}{line_str}  [{m['kind']}]")
    return 0


def cmd_files(index, args) -> int:
    scope = getattr(args, "scope", "")
    count_only = getattr(args, "count", False)
    files = index.get_all_files(scope=scope)

    if count_only:
        if args.format == "json":
            print(json.dumps({"count": len(files)}))
        else:
            print(f"Count: {len(files)}")
        return 0

    if args.format == "json":
        limit = getattr(args, "limit", 50)
        offset = getattr(args, "offset", 0)
        page, meta = _paginate(files, limit, offset)
        output = {**meta, "files": page}
        print(json.dumps(output, indent=2))
    else:
        for f in files:
            print(f)
        print(f"\nTotal: {len(files)} files")
    return 0


def cmd_file(index, args) -> int:
    compact = getattr(args, "compact", False)
    count_only = getattr(args, "count", False)
    file_path = args.path
    symbols = index.get_symbols_in_file(file_path)

    if not symbols:
        # Try fuzzy file matching
        all_files = index.get_all_files()
        similar = difflib.get_close_matches(file_path, all_files, n=5, cutoff=0.4)
        error = {"error": f"No symbols found in file: {file_path}"}
        if similar:
            error["similar_files"] = similar
            error["hint"] = f"Similar files: {', '.join(similar)}"
        if args.format == "json":
            print(json.dumps(error))
        else:
            print(error["error"])
            if similar:
                print(f"Similar files: {', '.join(similar)}")
        return 1

    if count_only:
        if args.format == "json":
            print(json.dumps({"count": len(symbols), "file": file_path}))
        else:
            print(f"Count: {len(symbols)} symbols in {file_path}")
        return 0

    if args.format == "json":
        limit = getattr(args, "limit", 50)
        offset = getattr(args, "offset", 0)
        page, meta = _paginate(symbols, limit, offset)
        output = {
            "file": file_path,
            **meta,
            "results": [symbol_to_dict(s, compact) for s in page],
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"Symbols in {file_path}:")
        print(format_list_text(symbols))
    return 0


# --- Main ---

def parse_args(argv=None):
    # Shared parent parser so flags work both before and after subcommand.
    # Using SUPPRESS so subparser defaults don't overwrite main parser values.
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--format", choices=["text", "json"], default=argparse.SUPPRESS,
                        help="Output format (default: text).")
    shared.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    shared.add_argument("--scope", default=argparse.SUPPRESS,
                        help="Limit results to files under this path prefix.")
    shared.add_argument("--compact", action="store_true", default=argparse.SUPPRESS,
                        help="Omit empty fields in JSON output.")
    shared.add_argument("--limit", type=int, default=argparse.SUPPRESS,
                        help="Max results to return (default: 50).")
    shared.add_argument("--offset", type=int, default=argparse.SUPPRESS,
                        help="Skip first N results (default: 0).")
    shared.add_argument("--count", action="store_true", default=argparse.SUPPRESS,
                        help="Return only the count of matching results.")

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
    parser.add_argument("--no-sqlite", action="store_true",
                        help="Force in-memory XML index (skip SQLite).")
    parser.add_argument("--scope", default="",
                        help="Limit results to files under this path prefix.")
    parser.add_argument("--compact", action="store_true", default=False,
                        help="Omit empty fields in JSON output.")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max results to return (default: 50).")
    parser.add_argument("--offset", type=int, default=0,
                        help="Skip first N results (default: 0).")
    parser.add_argument("--count", action="store_true", default=False,
                        help="Return only the count of matching results.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # symbol
    p_symbol = subparsers.add_parser("symbol", help="Look up a symbol by name.", parents=[shared])
    p_symbol.add_argument("name", help="Symbol name to look up.")

    # callgraph
    p_cg = subparsers.add_parser("callgraph", help="Show call graph for a function.", parents=[shared])
    p_cg.add_argument("func", help="Function name.")
    p_cg.add_argument("--depth", type=int, default=2,
                      help="Max traversal depth (default: 2).")
    p_cg.add_argument("--direction", choices=["calls", "callers", "both"],
                      default="both", help="Graph direction (default: both).")
    p_cg.add_argument("--max-nodes", type=int, default=200,
                      help="Max nodes in graph before truncation (default: 200).")
    p_cg.add_argument("--exclude-kinds", nargs="*", default=None,
                      help="Symbol kinds to exclude from graph.")
    p_cg.add_argument("--include-macros", action="store_true", default=False,
                      help="Include macros/defines in call graph (excluded by default).")

    # body
    p_body = subparsers.add_parser("body", help="Extract function source code.", parents=[shared])
    p_body.add_argument("func", help="Function name.")

    # list
    p_list = subparsers.add_parser("list", help="List all documented symbols.", parents=[shared])
    p_list.add_argument("--kind", default=None,
                        help="Filter by kind (e.g., function, variable, typedef).")
    p_list.add_argument("--file", default=None,
                        help="Filter by file path (substring match).")

    # search
    p_search = subparsers.add_parser("search", help="Search symbols by pattern.", parents=[shared])
    p_search.add_argument("pattern", help="Search pattern.")
    p_search.add_argument("--regex", action="store_true",
                          help="Treat pattern as regex.")

    # stats
    subparsers.add_parser("stats", help="Show index summary statistics.", parents=[shared])

    # members
    p_members = subparsers.add_parser("members", help="List members of a compound (struct/class).", parents=[shared])
    p_members.add_argument("compound", help="Compound name (e.g., struct name).")

    # files
    subparsers.add_parser("files", help="List all indexed files.", parents=[shared])

    # file
    p_file = subparsers.add_parser("file", help="List symbols in a specific file.", parents=[shared])
    p_file.add_argument("path", help="File path to query.")

    args = parser.parse_args(argv)

    # The shared parent uses SUPPRESS so its defaults don't overwrite the
    # main parser's values.  Ensure sensible defaults here.
    if not hasattr(args, "format") or args.format is None:
        args.format = "text"
    if not hasattr(args, "verbose") or args.verbose is None:
        args.verbose = False
    if not hasattr(args, "scope") or args.scope is None:
        args.scope = ""
    if not hasattr(args, "compact"):
        args.compact = False
    if not hasattr(args, "limit"):
        args.limit = 50
    if not hasattr(args, "offset"):
        args.offset = 0
    if not hasattr(args, "count"):
        args.count = False

    return args


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

    # Select index backend: SQLite by default, in-memory with --no-sqlite
    use_sqlite = not getattr(args, "no_sqlite", False)
    index = None

    if use_sqlite:
        try:
            index = DoxygenSQLiteIndex(xml_dir)
            if args.verbose:
                print(f"Using SQLite index: {index.db_path}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: SQLite index failed ({e}), falling back to in-memory.", file=sys.stderr)
            index = None

    if index is None:
        try:
            index = DoxygenXMLIndex(xml_dir)
            if args.verbose:
                print("Using in-memory XML index.", file=sys.stderr)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    commands = {
        "symbol": cmd_symbol,
        "callgraph": cmd_callgraph,
        "body": cmd_body,
        "list": cmd_list,
        "search": cmd_search,
        "stats": cmd_stats,
        "members": cmd_members,
        "files": cmd_files,
        "file": cmd_file,
    }

    handler = commands.get(args.command)
    if handler:
        rc = handler(index, args)
        sys.exit(rc)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
