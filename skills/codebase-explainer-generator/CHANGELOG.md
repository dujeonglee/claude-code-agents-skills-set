# Changelog

All notable changes to codebase-explainer-generator are documented in this file.

## v1.1.0 (2026-03-02)

### Changed
- **Remove hard module count limit**: Replaced "Module count is between 5-20" verification with rationale-based check. Removed "Target 5-20 modules" guideline and "2-15 source files" lower bound from module-design.md. Sizing table no longer has hard caps.
- **Cohesion-based module definition**: Replaced "Prefer build-system evidence over heuristics" with four principled guidelines: high cohesion/low coupling, build-system as starting point (not final answer), functional decomposition, and cross-module edge minimization.
- **Multi-file per-module docs**: Modules exceeding 400 lines now split into subtopic files (`NN-<module>-overview.md`, `NN-<module>-api.md`, `NN-<module>-internals.md`). Per-file cap reduced from 100KB to ~400 lines (~10KB). Output structure and batching strategy updated accordingly.
- **Domain-aware flow selection**: Calltrace Step 1 rewritten to analyze project domain first (Step 1a), then derive domain-specific flows (Step 1b) instead of using a generic fixed list. Init/teardown added as mandatory bookends (Step 1c). Variant cross-check renumbered to Step 1d.

### Fixed
- **I1: Flow summary drift in 00-index.md**: Added CRITICAL instruction requiring flow summaries in section 7g to be copied verbatim from calltrace.md, preventing re-attribution of module annotations.
- **I2: Fabricated lifecycle functions**: Added mandatory doxygen verification for every function name in data-structures.md Lifecycle sections. Added "lifecycle functions" as claim type 7 in verification.md so the verification subagent catches any that slip through.

---

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
