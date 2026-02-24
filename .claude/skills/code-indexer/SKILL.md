---
name: c-code-indexer
description: >
  Index all C source code files (.c, .h) in a workspace using Universal Ctags
  (a real parser, not regex) to catalog every function, struct, enum, union,
  typedef, macro, and global variable with their file paths and line ranges.
  Generates an indexing.md report. Use this skill whenever the user asks to
  "index C code", "catalog functions", "map codebase", "generate code index",
  "create function map", or wants to understand the structure of a C codebase.
  Also trigger for "indexing", "code map", "symbol index", "codebase overview".
---

# C Code Indexer Skill

Uses **Universal Ctags** — a battle-tested, widely-used open-source code indexing tool
(https://github.com/universal-ctags/ctags) — to parse C source files and extract symbols.

Universal Ctags has a dedicated C parser (not regex-based) that correctly handles macros,
multiline declarations, preprocessor conditionals, function pointers, and other C edge cases.
It's the same tool used by Vim, Emacs, VS Code, and the Linux kernel developers.

A thin Python wrapper converts the ctags JSON output into a clean `indexing.md` report.

## Prerequisites

```bash
# Ubuntu/Debian
apt-get install -y universal-ctags

# macOS
brew install universal-ctags

# Verify
/opt/homebrew/bin/ctags --version   # should say "Universal Ctags"
```

## Usage

### 1. Index the Workspace

```bash
python3 <skill_path>/scripts/ctags_index.py <workspace_path> [options]
```

### 2. Search for a Symbol

```bash
python3 <skill_path>/scripts/search_index.py <workspace> <name> [options]
```

The search utility automatically re-indexes if source files are newer than `indexing.md`.

### 3. Memory Management

```bash
# Clear memory of past failures
python3 <skill_path>/scripts/ctags_index.py <workspace> --clear-memory
```

## Options (ctags_index.py)

| Flag | Description | Default |
|------|-------------|---------|
| `--output`, `-o` | Output file path | `<workspace>/indexing.md` |
| `--exclude` | Comma-separated dir names to skip | `.git,build,node_modules,...` |
| `--no-headers` | Skip `.h` files | Disabled |
| `--kinds` | ctags C kinds to include | `dfegmstuv` (all) |
| `--no-stats` | Omit summary stats | Disabled |
| `--no-members` | Exclude struct/union members | Disabled |
| `--ctags-args` | Extra args passed to ctags | None |
| `--ctags-path` | **Full path to ctags executable** | `ctags` (in PATH) |
| `-v`, `--verbose` | Verbose output | Disabled |

## Options (search_index.py)

| Flag | Description | Default |
|------|-------------|---------|
| `--type` | Filter by symbol type (comma-separated) | None |
| `--exact-match` | Exact name match only | Disabled |
| `--show-all` | Show all matches, not just first per type | Disabled |
| `--ctags-path` | Full path to ctags executable | `ctags` (in PATH) |
| `--force-index` | Force re-indexing even if indexing.md exists | Disabled |
| `--index-only` | Only re-index, don't search | Disabled |
| `--indexing` | Path to indexing.md (when target is workspace) | `<workspace>/indexing.md` |
| `-v`, `--verbose` | Verbose output | Disabled |

## Kind Letters (ctags C kinds)

| Letter | Meaning |
|--------|---------|
| `d` | macro definitions |
| `e` | enumerators |
| `f` | function definitions |
| `g` | enum names |
| `m` | struct/union members |
| `s` | struct names |
| `t` | typedefs |
| `u` | union names |
| `v` | variable definitions |

## Examples (ctags_index.py)

```bash
# Index everything
python3 ctags_index.py ./src

# Functions and structs only, skip headers
python3 ctags_index.py ./src --kinds=fgs --no-headers

# With extra include paths for ctags preprocessor
python3 ctags_index.py ./src --ctags-args="--langmap=C:.c.h -I EXPORT_SYMBOL+"

# Use full path to ctags (ensures expected version is used)
python3 ctags_index.py ./src --ctags-path=/opt/homebrew/bin/ctags
```

## Examples (search_index.py)

```bash
# Search for a function by name (auto-reindex if stale)
python3 search_index.py ./src my_function

# Search with type filter
python3 search_index.py ./src slsi_urb --type function

# Search for struct with exact match
python3 search_index.py ./src my_struct --type struct --exact-match

# Force re-index when code has been modified
python3 search_index.py ./src my_function --force-index

# Only re-index without searching
python3 search_index.py ./src --index-only --ctags-path=/opt/homebrew/bin/ctags

# Search with full ctags path
python3 search_index.py ./src my_func --ctags-path=/opt/homebrew/bin/ctags --type function
```

## Output Format (indexing.md)

The generated `indexing.md` uses a consistent, name-sorted format:

```markdown
# Code Index

> **Generated**: 2026-02-24 16:50 | **Files**: 263 | **Symbols**: 22040
> **Tool**: Universal Ctags (AST-based parser)

> macro: 7187 | function: 5119 | struct: 383 | ...

---

| Name | File | Type | Lines | Detail |
|------|------|------|-------|--------|
| `my_function` | `src/file.c` | function | 42-100 | int |
| `my_struct` | `src/file.h` | struct | 10-25 |  |
```

## Workflow

1. Read this SKILL.md
2. Ensure Universal Ctags is installed: `brew install universal-ctags` (or apt-get)
3. Index: `python3 <skill_path>/scripts/ctags_index.py <workspace_path>`
4. Search: `python3 <skill_path>/scripts/search_index.py <workspace> <name>`

## Key Features

- **Consistent output format**: Name-sorted table for reliable parsing
- **Automatic re-indexing**: Detects stale indexing.md and re-indexes automatically
- **Full ctags path support**: Use `--ctags-path` to specify exact Universal Ctags version
- **Type filtering**: Search for symbols by specific type (function, struct, enum, etc.)
- **Exact match option**: Use `--exact-match` for precise name searches

## Memory Mechanism

This skill uses a memory file to remember past failures and avoid repeating them:

### Memory File Location
- `<workspace>/.claude/skill-cache/code-indexer.json`

### Memory Structure
```json
{
  "last_failure": {
    "timestamp": "2026-02-24T17:35:00Z",
    "error": "ctags not found",
    "command": "ctags --languages=C --kinds-C=dfegmstuv ..."
  },
  "known_issues": [
    {
      "pattern": "ctags not found",
      "solution": "Install Universal Ctags with 'brew install universal-ctags'"
    }
  ]
}
```

### How Memory Works

1. **Write on Failure**: When an indexing or search operation fails, the skill writes the error details to the memory file
2. **Read on Next Run**: Before running, the skill reads the memory file to check for known issues
3. **Prevent Repeated Errors**: If a known issue is detected, the skill suggests the solution before attempting the operation

### Example Memory Content

```json
{
  "last_failure": {
    "timestamp": "2026-02-24T16:50:00Z",
    "error": "FileNotFoundError: [Errno 2] No such file or directory: 'ctags'",
    "command": "ctags --languages=C --kinds-C=dfegmstuv --output-format=json ...",
    "workspace": "/path/to/workspace"
  },
  "suggested_ctags_path": "/opt/homebrew/bin/ctags",
  "last_success": {
    "timestamp": "2026-02-24T17:00:00Z",
    "files_indexed": 263,
    "symbols_found": 22040
  }
}
```

### Memory-Driven Actions

| Memory Condition | Action |
|-----------------|--------|
| `last_failure.error` contains "ctags not found" | Suggest `--ctags-path` option with known valid paths |
| `suggested_ctags_path` exists | Use that path automatically on next run |
| `last_success` is recent | Skip re-indexing unless files are modified |

### Memory File Management

The skill automatically manages the memory file:

- **Creates** memory file on first failure
- **Updates** on subsequent failures with new error details
- **Clears** successful run resets failure state
- **Persists** across agent restarts

## Workflow

1. Read this SKILL.md
2. Ensure Universal Ctags is installed: `brew install universal-ctags` (or apt-get)
3. Index: `python3 <skill_path>/scripts/ctags_index.py <workspace_path>`
4. Search: `python3 <skill_path>/scripts/search_index.py <workspace> <name>`