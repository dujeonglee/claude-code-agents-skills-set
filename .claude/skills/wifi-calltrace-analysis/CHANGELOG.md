# Changelog

## [0.4.0] — 2026-03-06

### Changed
- `SKILL.md` — one document per entry point + index
  - Phase 4 now writes `output/<entry_point>.md` (one file per entry)
  - Added Phase 5: index generation with cross-entry lock ordering and shared function overlap
  - Output directory structure documented with all intermediate and final files
  - Changed from `/tmp/` to workspace-relative `output/` (fixes subagent write permissions)
  - Added `output/.gitignore` to exclude generated files from version control

## [0.3.0] — 2026-03-06

### Changed
- `scripts/extract_calltrace.py` — switched from cscope to clang preprocessing + built-in C tokenizer
  - Uses `clang -E` to preprocess source files (resolves `#ifdef`, expands macros)
  - Built-in C tokenizer parses the preprocessed output (guaranteed balanced braces)
  - Falls back to raw source parsing if clang is unavailable
  - Added `--include` / `-I` and `--define` / `-D` CLI options for clang include paths and defines
  - Prerequisite changed: `clang` (instead of `cscope`)
- `SKILL.md` — updated Phase 1 to document clang-based workflow and new CLI options

## [0.2.0] — 2026-03-05

### Added
- `scripts/extract_calltrace.py` — call chain extraction with built-in C tokenizer (Python 3.8+)
  - Auto-detects entry points from cfg80211_ops, netdev_ops, mac80211_ops, NAPI/ISR registrations
  - BFS call chain traversal with configurable max depth
  - Deferred execution trigger detection (napi_schedule, schedule_work, etc.)
  - Noise filtering for macros, logging, and kernel leaf functions

### Changed
- `SKILL.md` — redesigned for source-directory-first workflow
  - Phase 1 now runs extract_calltrace.py as primary input method
  - Raw trace text supported as legacy fallback
  - Phase 2 split into 2a (context analysis) + 2b (lock analysis) for smaller subagent context
  - Added combined Phase 2 JSON schema for Phase 3 input contract
  - Inlined format detection table in Phase 1 for orchestrator self-sufficiency
- Fixed all stale "Topic 08" references to correct post-renumbering numbers

## [0.1.0] — 2026-03-04

### Added
- `SKILL.md` — orchestration for kernel call trace analysis
- `topics/00-calltrace-parsing.md` — R1 call order + R2 context tracking
- `topics/01-deferred-context.md` — R2-EXT deferred/async linkage (key requirement)
- `topics/02-locks-and-functions.md` — R3 lock analysis + R4 per-function analysis
- `topics/03-calltrace-output-format.md` — O1–O4 deliverable format specification
- `topics/04-wifi-domain-knowledge.md` — WiFi domain reference (shared)
