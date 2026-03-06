# Changelog

## [0.6.1] — 2026-03-07

### Fixed
- `extract_calltrace.py` — fixed Tier 1 regex: `\$` → `[$]` to match literal
  `$` in Makefile lines (`ifeq_re`, `obj_re`, `ccflags_re`)
- `extract_calltrace.py` — fixed Tier 1 parser: scan every `ifeq` independently
  instead of skipping lines consumed by outer blocks (nested `ifeq` within
  `else` branches was being missed)
- `extract_calltrace.py` — deduplicated `defines` lists in variant output
  using `dict.fromkeys()` to preserve order

## [0.6.0] — 2026-03-07

### Added
- Per-variant call graph analysis (O5 deliverable)
  - Phase 0: three-tier variant detection (`--detect-variants` flag)
    - Tier 1: Makefile `ifeq/else` blocks that switch `.o` file targets
    - Tier 2: Makefile `ccflags` `-D` defines
    - Tier 3: Source `#ifdef CONFIG_*` blocks above line count threshold
    - Kconfig `select` dependency resolution and variant grouping
  - Phase 1: runs extraction separately per variant with different `-D` flags
  - Phase 6: compares variant JSON outputs — entry point availability matrix,
    shared entry point node/edge diffs, variant-exclusive entry point listing
  - Output: `variant_comparison.md` with O5 deliverable
- `extract_calltrace.py` — added `--variant` (`-V`) flag to tag output JSON
  with a variant name; added `variant` and `defines` fields to output JSON
- `extract_calltrace.py` — added `--detect-variants` flag and `--min-lines`
  flag (default 50) for three-tier variant detection
- `topics/05-per-variant-callgraph.md` — instructions for variant
  identification, multi-extraction workflow, and O5 output format

## [0.5.0] — 2026-03-06

### Added
- `scripts/validate_output.py` — hallucination detection for subagent output
  - Validates Phase 2a, 2b, and final Markdown against extraction ground truth
  - Checks: invented functions, invalid layers/contexts, missing deferred triggers,
    hallucinated callees, lock scope correctness, tag ID format, section completeness
  - Supports per-entry (`--entry`) and batch (`--all`) validation modes
  - Exit codes: 0 (pass), 1 (errors), 2 (warnings only)
- `SKILL.md` — added Phase 4b (validation) between assembly and index generation

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
