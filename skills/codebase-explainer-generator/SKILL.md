# Codebase Explainer Generator

Generate a tailored codebase-explainer skill for any project. Point it at a workspace and it produces per-module architecture docs that Claude can reference in future sessions.

## Triggers

- generate codebase explainer
- create architecture skill
- analyze codebase architecture
- explain this codebase
- generate architecture docs

## Quick Start

```
"Generate a codebase explainer for pcie_scsc"
"Create architecture docs for this project"
"Analyze and document the codebase architecture"
```

## Script Reference

### analyze.py — Codebase Data Gathering

```bash
python3 scripts/analyze.py <workspace> [options]

Options:
  --format json|text     Output format (default: json)
  --max-depth N          Directory tree depth (default: 4)
  --exclude <dirs>       Extra directories to exclude
  --language <lang>      Override auto-detection (c|c++|python|java|go|rust|js|ts|auto)
  --output <path>        Write to file instead of stdout
```

**Output**: Structured JSON with project stats, detected modules, key files, entry points, and directory tree. No external dependencies — pure Python 3.8+.

---

## Agent Workflow

When triggered, follow these five phases in order.

### Phase 1: Analysis

1. **Determine workspace**: Ask the user or infer from context. Resolve to an absolute path.

2. **Run the analysis script**:
   ```bash
   python3 scripts/analyze.py <workspace> --format json --output /tmp/codebase-analysis.json
   ```

3. **Read and review** the output in `/tmp/codebase-analysis.json`:
   - Note the detected language, build system, total files/lines
   - Review the module list — these become the per-module doc targets
   - Check key files and entry points for accuracy

4. **Refine modules** if the automatic detection missed important boundaries:
   - Read the build system files (Makefile, Cargo.toml, package.json, etc.) — they reveal the true module structure
   - Merge modules that are too fine-grained (e.g., multiple prefix clusters that belong together)
   - Split modules that are too coarse (e.g., a directory with distinct subsystems)
   - Target 5–20 modules for documentation (see sizing guidelines below)

### Phase 2: Symbol Enrichment (optional)

Skip this phase for small projects (<2000 LOC) or if no doxygen-generator skill is available.

5. **Check if doxygen-generator is available**: Look for `skills/doxygen-generator/` in the workspace or skill path.

6. **Generate doxygen index if missing**: If the workspace has no `.doxygen/` directory:
   ```bash
   python3 skills/doxygen-generator/scripts/generate.py <workspace> --language <lang>
   ```

7. **Query doxygen for enrichment data**:
   ```bash
   # Overall stats
   python3 skills/doxygen-generator/scripts/query.py <workspace> stats

   # Key struct definitions
   python3 skills/doxygen-generator/scripts/query.py <workspace> members <struct_name> --format json

   # Function signatures and call graphs for important functions
   python3 skills/doxygen-generator/scripts/query.py <workspace> symbol <function_name>
   python3 skills/doxygen-generator/scripts/query.py <workspace> callgraph <function_name> --depth 2
   ```

### Phase 3: Deep Exploration (per module)

8. **For each module, launch parallel Explore agents** (up to 5 at a time):
   - Read the key files in the module (start with headers, then source)
   - Identify: purpose, key data structures, public API, internal flow
   - Note cross-module dependencies (which other modules does this one call?)
   - Use doxygen query results to enrich understanding if available

9. **Build a mental model** of the overall architecture:
   - How do modules relate? What are the layers?
   - What is the data flow from entry point to output?
   - What are the key abstractions and design patterns?

### Phase 4: Documentation Writing

10. **Create the output directory**:
    ```
    skills/<project-name>-explainer/
    ```

11. **Write `00-index.md`** — Architecture Overview:
    - Project name, language, build system, scale (files/lines)
    - Layer/architecture diagram (ASCII)
    - Module map table: name, file count, purpose (one line each)
    - Key data structures summary (name, purpose, key fields)
    - Cross-module data flow overview
    - Glossary of project-specific terms

12. **For each module, write `NN-<module-name>.md`**:
    - Module purpose and scope (1-2 paragraphs)
    - File inventory: each file with one-line purpose
    - Key data structures (with field descriptions)
    - API surface: public functions with signatures and brief descriptions
    - Internal flow (ASCII diagrams where helpful)
    - Configuration options affecting this module
    - Dependencies: which modules this one uses and which depend on it

13. **Write `SKILL.md`** for the generated explainer skill:
    ```markdown
    # <Project> Codebase Explainer

    ## Triggers
    - explain <project> codebase
    - how does <project> work
    - <project> architecture
    - what does <module> do in <project>

    ## Module Map
    | Module | Files | Doc | Purpose |
    |--------|-------|-----|---------|
    | ...    | ...   | ... | ...     |

    ## How to Use
    1. For overall architecture → read 00-index.md
    2. For a specific module → find it in the table, read the linked doc
    3. For a specific function → use doxygen-generator's query.py

    ## Key Data Structures
    - `struct_name` — purpose (see 00-index.md)
    - ...
    ```

14. **Write `analysis.json`** — Copy the raw analysis data into the output directory for future regeneration.

### Phase 5: Verification

15. **Check file sizes**: Each doc must be under 100KB. If any exceeds this:
    - Split into sub-module docs (e.g., `02a-hip-core.md`, `02b-hip-transport.md`)

16. **Report results** to the user:
    - List all files created with sizes
    - Module count and total documentation size
    - Any modules that were skipped or need manual review
    - Suggest next steps (e.g., "review 00-index.md for accuracy")

---

## Sizing Guidelines

| Codebase Size | Files | Modules to Doc | Output Files |
|---------------|-------|-----------------|--------------|
| Tiny          | <20   | Skip per-module | 2 (index + SKILL.md) |
| Small         | 20-50 | 3-5 modules     | 5-7 |
| Medium        | 50-200| 5-12 modules    | 7-14 |
| Large         | 200-500| 10-20 modules  | 12-22 |
| Very large    | 500+  | 15-20 max       | 17-22 |

For very large codebases (>500 files), focus on the most architecturally significant 15-20 modules. Mention remaining modules briefly in the index.

---

## Agent Tips

- **Always read build system files first** — Makefile, Cargo.toml, package.json reveal the true module structure better than any heuristic.
- **Cap each architecture doc at 100KB** — split further if needed. Claude's context window benefits from focused, modular docs.
- **Use doxygen-generator when available** — symbol-level data (struct fields, call graphs) dramatically improves doc quality.
- **Prefer ASCII diagrams over prose** for data flows and layer architectures — they're more scannable.
- **Cross-reference between docs** — use relative links like `[HIP subsystem](02-hip-subsystem.md)` so Claude can navigate.
- **Include the "why" not just the "what"** — architecture docs should explain design decisions, not just enumerate files.
- **For flat directory codebases** — the prefix clustering and include-graph analysis become more important. Read the analysis output carefully.
- **Module detection is heuristic** — always review and adjust. The script suggests; you decide.

---

## Generated Skill Usage

Once created, the generated `<project>-explainer/` skill works independently:
- No runtime dependency on this generator or doxygen-generator
- Claude reads the SKILL.md and architecture docs when triggered
- The docs serve as a navigational aid — Claude still reads actual source code for details
- Regenerate by re-running this workflow if the codebase changes significantly
