# Changelog

## [0.1.0] — 2026-03-04

### Added — Module Inventory Skill
- `SKILL-module-inventory.md` — orchestration for WiFi driver module analysis
- `scripts/scan_source.py` — deterministic source-tree scanner (Python 3.8+, no deps)
- `topics/01-module-identification.md` — Rules 1–5 for boundary detection
- `topics/02-module-entry-format.md` — two-layer entry format specification
- `topics/03-cohesion-and-issues.md` — cohesion rating criteria + 5 issue labels

### Added — Calltrace Analysis Skill
- `SKILL-calltrace-analysis.md` — orchestration for kernel call trace analysis
- `topics/04-calltrace-parsing.md` — R1 call order + R2 context tracking
- `topics/05-deferred-context.md` — R2-EXT deferred/async linkage (key requirement)
- `topics/06-locks-and-functions.md` — R3 lock analysis + R4 per-function analysis
- `topics/07-calltrace-output-format.md` — O1–O4 deliverable format specification

### Added — Shared
- `topics/08-wifi-domain-knowledge.md` — WiFi domain reference (both skills)
