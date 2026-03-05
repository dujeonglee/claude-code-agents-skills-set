# WiFi Driver Module Inventory Skill

Analyzes a Linux WiFi driver source directory and produces a Module Inventory —
a structured, human-readable catalog of every logical module in the driver.

---

## Triggers

Activate this skill when the user:
- Provides a path to a WiFi driver source directory and asks for module analysis
- Asks to "analyze the modules" or "create a module inventory" for a driver
- Asks "what modules are in this driver" or "how is this driver organized"
- Provides a path containing `*.c`, `*.h`, and/or `Makefile` and asks for architecture analysis

---

## Quick Start

```
User: Analyze the modules in /path/to/wifi/driver
```

The skill will:
1. Run `scan_source.py` to collect deterministic data
2. Identify module boundaries using scan data + domain knowledge
3. Write a two-layer entry (Overview + Technical Detail) for each module
4. Assemble the final Module Inventory document

---

## Output Format

A single Markdown document:

```markdown
# Module Inventory: <driver name>
> <driver path> | <file count> files | <total LOC> lines | <date>

---

## 1. <Module Name>
### Overview
...
### Technical Detail
...

---

## 2. <Module Name>
...
```

Modules are ordered from most foundational (depended on by many) to most
concrete (depends on many).

---

## Workflow

### Phase 1: Data Collection

Run the scan script to produce deterministic data:

```bash
python3 scripts/scan_source.py <driver_path> --output /tmp/scan_data.json
```

The script produces JSON containing:
- File inventory with LOC per file
- `#include` edges (local vs. kernel)
- Function-name prefix frequency
- Makefile `obj-y` / `obj-$(CONFIG_*)` targets
- Struct definitions per header

**Read the JSON output before proceeding.** All subsequent analysis uses this data.

### Phase 2: Module Identification

Launch a subagent to identify module boundaries.

**Subagent instructions:**
- Read: `topics/00-module-identification.md` (boundary detection rules)
- Read: `topics/03-wifi-domain-knowledge.md` (domain reference)
- Input: The scan data JSON from Phase 1
- Output: `module_boundaries.json` — list of modules with files, responsibility, evidence

**Verification after Phase 2:**
- Every file in the scan data is assigned to exactly one module
- Every module has a responsibility sentence starting with a verb
- No module's responsibility requires "and" between unrelated concerns
- Aim for 5–15 modules total

### Phase 3: Module Entry Writing

Launch subagent(s) to write the full Markdown entries.

**Subagent instructions:**
- Read: `topics/01-module-entry-format.md` (exact format specification)
- Read: `topics/02-cohesion-and-issues.md` (cohesion rating + issue labels)
- Input: `module_boundaries.json` from Phase 2 + scan data JSON
- Output: Markdown text for each module entry

**Batching for large drivers (>10 modules):**
Split into batches of 4–5 modules per subagent call. Provide the full module
list to each batch so cross-module dependencies can be resolved.

**Verification after Phase 3:**
- Every entry has both Overview and Technical Detail sections
- Every Responsibility starts with a verb
- Every External Dependencies row names a specific function, struct, or header
- Cohesion ratings are STRONG, MODERATE, or WEAK (no other values)
- Issues section is omitted entirely when clean (not empty, omitted)

### Phase 4: Assembly & Acceptance Checks

Assemble the final document:
1. Write the document header with driver name, path, file count, LOC, date
2. Order modules from most foundational to most concrete
3. Insert `---` separators between modules

**Run acceptance checks AC-1 through AC-7:**

| Check | Criterion | How to Verify |
|-------|-----------|---------------|
| AC-1 | Every module has Overview + Technical Detail, no empty fields | Scan each entry |
| AC-2 | Every Responsibility starts with a verb, is one sentence | Regex check |
| AC-3 | Every file assigned to exactly one module | Count vs scan data |
| AC-4 | Every External Dep row names a specific function/struct/header | Scan dep tables |
| AC-5 | Cohesion ratings consistent with criteria in Topic 03 | Cross-check |
| AC-6 | Issues section omitted when no issues | Check for empty Issues |
| AC-7 | Overall readability — would a new engineer understand the driver? | Final read-through |

If any check fails, fix the entry before presenting to the user.

---

## Out of Scope

These are intentionally excluded. Do not generate them even if they seem useful:

| Excluded Item | Reason |
|---------------|--------|
| SAM score, MCD score, or any aggregate numeric score | Scores compress per-module detail |
| Mermaid or other dependency diagrams | External Dependencies tables are more precise |
| Improvement roadmap or refactoring backlog | Issues section per module is sufficient |
| Inter-module dependency matrix (N×N table) | Redundant with per-module dep tables |
| Lines of code or file count metrics per module | Size doesn't explain what a module does |

Exception: Total driver LOC appears in the document header for orientation.

---

## DO

- DO run `scan_source.py` first — never skip the data collection step.
- DO verify every file is assigned after Phase 2.
- DO order modules from foundational to concrete in the final output.
- DO run all seven acceptance checks before presenting the result.
- DO use subagents for Phases 2 and 3 — keep the orchestration lightweight.
- DO read the topic files in the subagent, not in the main context.

## DON'T

- DON'T generate dependency diagrams, scores, or roadmaps — they are out of scope.
- DON'T skip the scan script and try to read source files directly — the script
  produces deterministic data that the LLM cannot reliably compute.
- DON'T write module entries without the scan data — guessing file contents leads to errors.
- DON'T present the inventory without running acceptance checks.
- DON'T include a "Summary" or "Conclusion" section — the inventory speaks for itself.
- DON'T add sections or fields not specified in Topic 02 — the format is fixed.
