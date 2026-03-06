# doxygen-generator: Improvement Report

Tested against Linux kernel scheduler (`kernel/sched/`, 45 files) and e1000 driver
(`drivers/net/ethernet/intel/e1000/`, 7 files) using both SQLite and in-memory backends.

---

## P0 — Bugs / Broken Behavior

### 1. Exit code 0 on "symbol not found" — agents can't detect failures

```
$ query.py /ws body nonexistent --format json; echo $?
{"error": "Symbol not found: nonexistent"}
0
```

**Impact**: An AI agent running `subprocess.run()` gets exit code 0 and must parse
JSON to discover the error.  Every other failure path (`workspace not found`,
`XML dir missing`) returns exit 1.

**Fix**: Return exit code 1 whenever the output contains `{"error": ...}`.
Or add a top-level `"ok": true/false` field so agents can check without parsing
the entire payload.

### 2. No pagination / `--limit` — output blows up agent context windows

```
$ query.py /ws list --kind function --format json | wc -c
1162986          # 1.1 MB for just the scheduler!

$ query.py /ws callgraph schedule --depth 5 --format json | wc -c
756192           # 756 KB for a single callgraph

$ query.py /ws search "*" --format json | wc -c
1569856          # 1.5 MB for wildcard
```

**Impact**: An agent with a 128K context window that runs `list --kind function`
against even a moderate subsystem will truncate or OOM its context.  This is the
single biggest usability issue for AI agents.

**Fix**: Add `--limit N` (default: 50?) and `--offset N` to `list`, `search`,
and `symbol` subcommands.  Also add `--count` mode that returns only the count.
JSON output should include `{"total": 2079, "offset": 0, "limit": 50, "results": [...]}`.

---

## P1 — Major UX Friction for Agents

### 3. No `stats` / summary subcommand — agent must list-all to understand the codebase

An agent's first instinct is "how big is this codebase and what's in it?" but
there's no cheap way to answer that.  `list --format json` dumps everything.

**Fix**: Add `query.py /ws stats` that returns:
```json
{
  "total_symbols": 2922,
  "by_kind": {"function": 2079, "variable": 456, "define": 312, ...},
  "by_file": {"kernel/sched/core.c": 285, "kernel/sched/fair.c": 340, ...},
  "index_backend": "sqlite",
  "db_size_bytes": 5607424,
  "xml_files": 52
}
```
This lets the agent decide whether to scope down before querying.

### 4. No fuzzy / "did you mean?" on symbol lookup

```
$ query.py /ws symbol shed_init --format json
{"error": "Symbol not found: shed_init"}
```

No suggestion that `sched_init` exists.  Agents (and LLMs especially) make
spelling mistakes.

**Fix**: On "not found", compute edit-distance against the symbol index and
return up to 5 close matches:
```json
{"error": "Symbol not found: shed_init", "did_you_mean": ["sched_init", "sched_init_smp"]}
```

### 5. No struct member enumeration — "list members of struct rq" is impossible

```
$ query.py /ws symbol rq --format json
# Returns the struct as a compound with kind="struct", but NO member list.
# Members appear as top-level "variables" with no parent association.
```

For kernel code, understanding struct layouts is critical.  There's no way to
ask "what fields does `struct rq` have?"

**Fix**: Add `query.py /ws members <compound_name>` subcommand that parses
the compound XML and returns members with types, offsets, and brief descriptions.

### 6. `--kind` accepts invalid values silently — returns empty with no hint

```
$ query.py /ws list --kind struct --format json
[]
```

No error, no hint about valid kinds.  Agent has to guess.

**Fix**: Either validate `--kind` against known values from the index, or on
empty results include `{"results": [], "valid_kinds": ["function","variable","define",...]}`.

### 7. generate.py dumps 569 Doxygen warnings into verbose output with no summary

```
$ generate.py /ws --force -v 2>&1 | grep -c "warning:"
569
```

The verbose output is 1.4 MB mostly of warnings.  An agent can't extract
useful info from this.

**Fix**:
- Always print a summary: `"569 Doxygen warnings (use -v to see details)"`
- With `-v`, still show them but also print the summary at the end
- Optionally write warnings to a file: `.doxygen/warnings.log`

---

## P2 — Improvement Opportunities

### 8. No `--count` mode for cheap cardinality checks

Before running `list`, an agent wants to know "how many functions are there?"
without downloading the full list.  Currently must parse the entire output.

**Fix**: Add `--count` flag that returns `{"count": 2079}` for `list` and
`search`.

### 9. Callgraph depth is unbounded in practice — no size safeguard

`callgraph schedule --depth 5` returns 756 KB.  The Linux kernel call graph
with depth 10 would be enormous.  No warning or cap.

**Fix**: Add `--max-nodes N` (default: 200?) that truncates the graph and
includes `"truncated": true, "total_nodes": 1523` in the output.

### 10. No way to list available files in the index

An agent using `--scope` or `--file` needs to know what files exist.  Currently
must run `list --format json` and deduplicate the `file` field.

**Fix**: Add `query.py /ws files` subcommand:
```json
["kernel/sched/core.c", "kernel/sched/fair.c", ...]
```
Or include it in the `stats` output.

### 11. Callgraph includes macros as nodes (EXPORT_SYMBOL, SM_NONE)

```
schedule -> SM_NONE [define]      # Not a real function call
schedule <- EXPORT_SYMBOL [function]  # Export macro, not a caller
```

These pollute the call graph with noise.

**Fix**: Add `--exclude-kinds define` to callgraph, or auto-filter macros
by default with `--include-macros` to opt in.

### 12. Compound symbols (struct, class) have no file/line in SQLite backend

```
$ query.py /ws symbol rq  # kind=struct → file="", line=0
```

The compound's location is available in the XML but not inserted into the DB.

**Fix**: During `_build_db()`, parse `<compounddef>` location elements and
populate file/line for compound rows.

### 13. No machine-readable output from generate.py

`generate.py` prints human text to stdout.  An agent calling it via subprocess
must regex-parse "Documentation generated successfully."

**Fix**: Add `--format json` to `generate.py` that returns:
```json
{
  "status": "ok",
  "output_dir": "/ws/.doxygen",
  "html": true,
  "xml": true,
  "graphs": true,
  "warnings": 569,
  "elapsed_seconds": 12.3,
  "files_processed": 45,
  "symbols_indexed": 2922
}
```

### 14. No way to query by file ("what functions are defined in core.c?")

`--file` on `list` does substring matching, but agents want exact file queries.
Also, `--scope` and `--file` do different things (prefix vs substring) which
is confusing.

**Fix**: Rename for clarity or add `query.py /ws file kernel/sched/core.c`
subcommand that returns all symbols in that file, sorted by line.

### 15. JSON output includes empty arrays for every symbol

Every symbol in `list` output carries `"references": [], "referenced_by": []`
even when empty.  For 2079 functions this wastes ~40 KB of JSON.

**Fix**: Add `--compact` flag that omits empty fields, or make `list` output
a lightweight schema by default (name, kind, file, line) with a `--full` flag
for the complete SymbolInfo.

---

## Summary Prioritization

| Priority | Item | Effort | Impact for Agents |
|----------|------|--------|-------------------|
| P0 | #2 `--limit/--offset` pagination | Medium | Critical — context window |
| P0 | #1 Non-zero exit on "not found" | Trivial | Scripting correctness |
| P1 | #3 `stats` subcommand | Small | First-query experience |
| P1 | #4 "Did you mean?" suggestions | Medium | Error recovery |
| P1 | #13 JSON output from generate.py | Small | Pipeline automation |
| P1 | #8 `--count` mode | Trivial | Cheap cardinality |
| P1 | #15 Compact JSON / lightweight list | Small | Context efficiency |
| P2 | #5 Struct member enumeration | Medium | Kernel data structures |
| P2 | #9 Callgraph size cap | Small | Context protection |
| P2 | #10 `files` subcommand | Trivial | Discoverability |
| P2 | #11 Filter macros from callgraph | Small | Signal-to-noise |
| P2 | #12 Compound locations in SQLite | Trivial | Data completeness |
| P2 | #6 Validate `--kind` values | Trivial | Error messages |
| P2 | #7 Warning summary | Small | generate.py UX |
| P2 | #14 Per-file query subcommand | Small | Navigation |
