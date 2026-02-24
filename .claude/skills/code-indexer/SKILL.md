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
ctags --version   # should say "Universal Ctags"
```

## Usage

```bash
python3 <skill_path>/scripts/ctags_index.py <workspace_path> [options]
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--output`, `-o` | Output file path | `<workspace>/indexing.md` |
| `--exclude` | Comma-separated dir names to skip | `.git,build,node_modules,...` |
| `--no-headers` | Skip `.h` files | Disabled |
| `--kinds` | ctags C kinds to include | `dfegmstuv` (all) |
| `--sort` | Sort: `file`, `name`, `type` | `file` |
| `--no-stats` | Omit summary stats | Disabled |
| `--no-members` | Exclude struct/union members | Disabled |
| `--ctags-args` | Extra args passed to ctags | None |

### Kind Letters (ctags C kinds)

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

### Example

```bash
# Index everything
python3 ctags_index.py ./src

# Functions and structs only, skip headers
python3 ctags_index.py ./src --kinds=fgs --no-headers --sort=type

# With extra include paths for ctags preprocessor
python3 ctags_index.py ./src --ctags-args="--langmap=C:.c.h -I EXPORT_SYMBOL+"
```

## Workflow

1. Read this SKILL.md
2. Ensure Universal Ctags is installed: `apt-get install -y universal-ctags`
3. Run `python3 <skill_path>/scripts/ctags_index.py <workspace_path>`
4. Present the generated `indexing.md` to the user
