# Changelog

All notable changes to codebase-explainer-generator are documented in this file.

## v1.0.0 (2026-03-02)

Initial versioned release. Captures all development from the initial commit through
`ef8a03bab6d26ed53988c73dd0572e0d202299e7`.

### Features
- 6-phase agent pipeline: data collection, module design, topic analysis, documentation writing, verification, fix & finalize
- `analyze.py` script for codebase data gathering (file inventory, include edges, build system detection, variant detection)
- Subagent-based architecture with dedicated topic files for module-design, data-structures, calltrace, and verification
- Incremental update workflow (U1-U7) for selective doc regeneration on codebase changes
- Compile-time variant detection (conditional includes, function pairs, Makefile conditionals)
- Per-module doc sizing formula: `target_lines = 120 + (file_count × 15) + (total_lines ÷ 200)`
- Module complexity bounds (5-20 modules)
- Doxygen-backed verification phase to catch hallucinations
- Generated SKILL.md with self-contained incremental update instructions

### Tested
- pcie_scsc (263 files, 191,476 LOC, C/Linux kernel module): 96% overall accuracy, 8 corrections applied
