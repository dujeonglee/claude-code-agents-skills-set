---
skill: doxygen-generator
trigger:
  - generate docs
  - create documentation
  - show call graph
  - find function callers
  - browse API docs
  - show function body
  - doxygen
  - documentation server
  - symbol lookup
  - who calls
  - dependency graph
  - cross-reference
  - document code
  - function reference
---

# Doxygen Generator Skill

Generate browsable HTML documentation, query call graphs, look up symbols, and extract function bodies — all from Doxygen's XML output. **Zero external dependencies**: all binaries are bundled.

## Overview

This skill provides a complete documentation pipeline:

1. **Generate** — Run Doxygen to produce HTML docs + XML index from C/C++/Java/Python source
2. **Query** — Programmatically look up symbols, trace call graphs, extract function bodies
3. **Hook** — Auto-regenerate on `git push` via managed git hooks

All tools use bundled Doxygen and Graphviz (dot) binaries — nothing to install.

## Prerequisites

**None.** Binaries are bundled in `bin/` per platform. See `bin/README.md` for details.

On macOS, if you see a Gatekeeper warning, run:
```bash
xattr -cr <skill-path>/bin/macos-arm64/
```

## Quick Start

```bash
SKILL="<path-to-this-skill>"
WORKSPACE="<path-to-your-project>"

# 1. Generate documentation
python3 "$SKILL/scripts/generate.py" "$WORKSPACE"

# 2. Query a symbol
python3 "$SKILL/scripts/query.py" "$WORKSPACE" symbol main

# 3. Show call graph
python3 "$SKILL/scripts/query.py" "$WORKSPACE" callgraph main --depth 3
```

## Script Reference

### `scripts/platform.py` — Platform Detection

Detects OS/architecture and resolves bundled binary paths. Used internally by other scripts.

```bash
python3 scripts/platform.py    # Print diagnostics
```

### `scripts/generate.py` — Generate Documentation

```
python3 generate.py <workspace> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--project-name` | dir name | Project title in generated docs |
| `--output-dir` | `.doxygen` | Output directory (relative to workspace) |
| `--input-dirs` | workspace root | Space-separated input directories |
| `--language` | `c` | One of: `c`, `c++`, `java`, `python`, `auto` |
| `--file-patterns` | per language | Override file patterns (e.g., `"*.c *.h"`) |
| `--exclude` | common dirs | Directories to exclude |
| `--doxyfile` | generated | Use a custom Doxyfile |
| `--no-html` | false | Skip HTML generation |
| `--no-xml` | false | Skip XML generation |
| `--no-graphs` | false | Disable call/caller graphs |
| `--force` | false | Regenerate even if up-to-date |
| `--clear-cache` | false | Clear cached state first |
| `-v` | false | Verbose output |

**Output:** `<workspace>/.doxygen/html/` and `<workspace>/.doxygen/xml/`

### `scripts/query.py` — Query Documentation

```
python3 query.py <workspace> <subcommand> [args] [options]
```

**Global options:** `--output-dir`, `--xml-dir`, `--format text|json`, `-v`

| Subcommand | Usage | Description |
|-----------|-------|-------------|
| `symbol <name>` | `query.py /ws symbol my_func` | Look up symbol: kind, file, line, signature, calls, callers |
| `callgraph <func>` | `query.py /ws callgraph main --depth 2` | Tree-formatted call graph |
| `body <func>` | `query.py /ws body process_data` | Extract function source code |
| `list` | `query.py /ws list --kind function` | List all symbols, filter by kind/file |
| `search <pat>` | `query.py /ws search "process*"` | Glob/substring/regex search across symbols |

#### `symbol` — Look up a symbol

```bash
python3 query.py /workspace symbol main
```

Returns: name, kind, file, line, return type, parameters, brief description, calls, and callers.

#### `callgraph` — Call graph

```bash
python3 query.py /workspace callgraph main --depth 3 --direction calls
```

Options:
- `--depth N` — Max traversal depth (default: 2)
- `--direction calls|callers|both` — Graph direction (default: both)

#### `body` — Extract function body

```bash
python3 query.py /workspace body process_data
```

Reads the source file directly using line ranges from the XML metadata. Preserves original formatting with line numbers.

#### `list` — List symbols

```bash
python3 query.py /workspace list --kind function --file main.c
```

Options:
- `--kind <kind>` — Filter: function, variable, typedef, enum, define, etc.
- `--file <path>` — Filter by file path (substring match)

#### `search` — Search symbols

```bash
python3 query.py /workspace search "init*"
python3 query.py /workspace search "^process_" --regex
```

Options:
- `--regex` — Treat pattern as regular expression

### `scripts/hook.py` — Git Hook Manager

```
python3 hook.py <workspace> install|remove|status|run [options]
```

| Command | Description |
|---------|-------------|
| `install` | Add auto-regeneration hook to `.git/hooks/pre-push` |
| `remove` | Remove the hook (preserves other hooks) |
| `status` | Check if hook is installed |
| `run` | Manually trigger regeneration |

Options: `--hook-type` (default: `pre-push`), `--skill-path`

## JSON Output

All query subcommands support `--format json` for machine-parseable output:

```bash
python3 query.py /workspace symbol main --format json
python3 query.py /workspace list --kind function --format json
```

## Configuration

### Language Defaults

| Language | File Patterns |
|----------|--------------|
| `c` | `*.c *.h` |
| `c++` | `*.c *.h *.cpp *.hpp *.cc *.hh *.cxx *.hxx` |
| `java` | `*.java` |
| `python` | `*.py` |
| `auto` | all of the above |

### Custom Doxyfile

You can use a fully custom Doxyfile:

```bash
python3 generate.py /workspace --doxyfile /path/to/Doxyfile
```

## Cache & Incremental Builds

The skill caches generation state in `<workspace>/.claude/skill-cache/doxygen-generator.json`. It compares source file modification times against the cache to skip unnecessary regeneration. Use `--force` to override, or `--clear-cache` to reset.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Doxygen binary not found" | Check `bin/README.md` for download instructions. Run `python3 scripts/platform.py` to verify paths. |
| "macOS Gatekeeper blocking" | Run `xattr -cr <skill-path>/bin/macos-arm64/` |
| "dot (Graphviz) not found" | Graphs will be auto-disabled. Place `dot` binary in `bin/<platform>/` for call graphs. |
| "XML directory not found" | Run `generate.py` first before using `query.py`. |
| No symbols in output | Check `--language` and `--file-patterns` match your source files. |
| Timeout (>600s) | Very large codebase. Try narrowing `--input-dirs` or `--exclude`. |

## Agent Workflow

Recommended sequence for a Claude Code agent:

1. **Generate docs** once per workspace:
   ```bash
   python3 "$SKILL/scripts/generate.py" "$WORKSPACE" --language c
   ```

2. **Explore structure** — list all functions:
   ```bash
   python3 "$SKILL/scripts/query.py" "$WORKSPACE" list --kind function
   ```

3. **Investigate specific functions** — look up details and call graph:
   ```bash
   python3 "$SKILL/scripts/query.py" "$WORKSPACE" symbol target_func
   python3 "$SKILL/scripts/query.py" "$WORKSPACE" callgraph target_func --depth 3
   ```

4. **Read source** — extract function body:
   ```bash
   python3 "$SKILL/scripts/query.py" "$WORKSPACE" body target_func
   ```

5. **Search** — find related symbols:
   ```bash
   python3 "$SKILL/scripts/query.py" "$WORKSPACE" search "init*"
   ```

6. **Install hook** for continuous updates:
   ```bash
   python3 "$SKILL/scripts/hook.py" "$WORKSPACE" install
   ```
