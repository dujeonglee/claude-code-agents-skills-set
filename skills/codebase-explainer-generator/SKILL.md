# Codebase Explainer Generator

Generate a tailored codebase-explainer skill for any project. Point it at a workspace and it produces architecture docs with data structure details, execution flow traces, and platform dependency annotations — optimized for consumption by porting agents.

## Triggers

- generate codebase explainer
- create architecture skill
- analyze codebase architecture
- explain this codebase
- generate architecture docs

## Quick Start

```
"Generate a codebase explainer for workspace"
"Create architecture docs for this project"
"Analyze and document the codebase architecture"
```

## Script Reference

### analyze.py — Codebase Data Gathering (truth only)

```bash
python3 scripts/analyze.py <workspace> [options]

Options:
  --format json|text     Output format (default: json)
  --max-depth N          Directory tree depth (default: 4)
  --exclude <dirs>       Extra directories to exclude
  --language <lang>      Override auto-detection (c|c++|python|java|go|rust|js|ts|auto)
  --output <path>        Write to file instead of stdout
```

**Output**: Structured JSON with file inventory, directory tree, include edges, key files, entry points, and config files. No module detection — that is done by the module-design subagent. No external dependencies — pure Python 3.8+.

### doxygen-generator — Symbol-level truth

Doxygen is a hard requirement for Phase 2+. It provides symbol definitions, struct members, call graphs, and file-to-symbol mappings that scripts cannot replicate.

The doxygen-generator skill is a **sibling** skill in the same skills directory. Paths are resolved at runtime in Phase 1.

---

## Agent Workflow

When triggered, follow these six phases in order. Phases use subagents (via the Agent tool) for judgment tasks — scripts produce truth, agents produce design.

### Phase 1: Data Collection (truth only)

No judgment in this phase — only run scripts and resolve paths.

1. **Determine workspace**: Ask the user or infer from context. Resolve to an absolute path. Store as `$WORKSPACE`.

2. **Resolve paths from `$WORKSPACE`**: All skills are deployed under `$WORKSPACE/.claude/skills/`. Derive:
   - `$SKILL_DIR` = `$WORKSPACE/.claude/skills/codebase-explainer-generator`
   - `$DOXYGEN_DIR` = `$WORKSPACE/.claude/skills/doxygen-generator`
   - `$OUTPUT_DIR` = `$WORKSPACE/.claude/skills/<project-name>-explainer`
   - Verify doxygen-generator exists: check that `$DOXYGEN_DIR/scripts/query.py` exists. If not, report an error and stop.

   All subsequent commands and subagent prompts must use these **absolute paths**.

3. **Create the output directory**: `mkdir -p $OUTPUT_DIR`

4. **Run the analysis script**:
   ```bash
   python3 $SKILL_DIR/scripts/analyze.py $WORKSPACE --format json --output $OUTPUT_DIR/analysis.json
   ```

5. **Read `analysis.json`** and note: language, build system, total files/lines, entry points.

6. **Generate doxygen index** (if not already present):
   ```bash
   python3 $DOXYGEN_DIR/scripts/generate.py $WORKSPACE --language <lang>
   ```

7. **Verify doxygen works**:
   ```bash
   python3 $DOXYGEN_DIR/scripts/query.py $WORKSPACE stats
   ```

8. **Report progress**: Tell the user — "Phase 1 complete. {total_files} files, {total_lines} lines, language: {language}. Doxygen index: {symbol_count} symbols."

### Phase 2: Module Design (single subagent)

Spawn **one** foreground Agent (subagent_type: general-purpose, do NOT use `run_in_background`) with this prompt (substitute all `$VARIABLES` with their resolved absolute paths):

> You are a module-design subagent. Read the topic skill file at `$SKILL_DIR/topics/module-design.md` for your full instructions.
>
> Inputs:
> - Analysis data: `$OUTPUT_DIR/analysis.json`
> - Workspace: `$WORKSPACE`
> - Doxygen query script: `$DOXYGEN_DIR/scripts/query.py`
>   Usage: `python3 $DOXYGEN_DIR/scripts/query.py $WORKSPACE <command> [args]`
>
> Output: Write `$OUTPUT_DIR/module_design.json`

**Wait for completion.** Read `module_design.json` and verify:
- Every file from `analysis.json` is assigned to exactly one module
- Module count is between 5-20
- Each module has a rationale with evidence
- `unassigned_files` is empty

If issues exist, fix them directly before proceeding.

**Report progress**: Tell the user — "Phase 2 complete. {module_count} modules designed, {unassigned_count} unassigned files."

### Phase 3: Topic Analysis (2 parallel subagents)

Spawn **two** foreground Agents in parallel (subagent_type: general-purpose, do NOT use `run_in_background`). Substitute all `$VARIABLES` with their resolved absolute paths:

**Data Structures subagent:**
> You are a data-structures subagent. Read the topic skill file at `$SKILL_DIR/topics/data-structures.md` for your full instructions.
>
> Inputs:
> - Analysis data: `$OUTPUT_DIR/analysis.json`
> - Module design: `$OUTPUT_DIR/module_design.json`
> - Workspace: `$WORKSPACE`
> - Doxygen query script: `$DOXYGEN_DIR/scripts/query.py`
>   Usage: `python3 $DOXYGEN_DIR/scripts/query.py $WORKSPACE <command> [args]`
>
> Output: Write your findings to `$OUTPUT_DIR/data-structures-draft.md`

**Calltrace subagent:**
> You are a calltrace subagent. Read the topic skill file at `$SKILL_DIR/topics/calltrace.md` for your full instructions.
>
> Inputs:
> - Analysis data: `$OUTPUT_DIR/analysis.json`
> - Module design: `$OUTPUT_DIR/module_design.json`
> - Workspace: `$WORKSPACE`
> - Doxygen query script: `$DOXYGEN_DIR/scripts/query.py`
>   Usage: `python3 $DOXYGEN_DIR/scripts/query.py $WORKSPACE <command> [args]`
>
> Output: Write your findings to `$OUTPUT_DIR/calltrace-draft.md`

**Wait for both to complete.** Read both draft files.

**Report progress**: Tell the user — "Phase 3 complete. {struct_count} key structures documented, {flow_count} execution flows traced."

### Phase 4: Documentation Writing (main agent synthesizes)

Using `analysis.json`, `module_design.json`, and the two draft files, write the final documentation:

7. **Write `00-index.md`** — Architecture Overview:
   - Project name, language, build system, scale (files/lines)
   - Layer/architecture diagram (ASCII)
   - Module map table: name, file count, purpose, **porting impact** (one line each)
   - Key data structures summary with platform dependency flags
   - Cross-module data flow overview
   - **Platform dependency triage table**: module, impact level, platform APIs used, porting notes
   - Glossary of project-specific terms

8. **Write `data-structures.md`** — from the draft:
   - Integrate the subagent's draft with module_design.json context
   - Ensure every key structure has platform-specific field annotations
   - Include the platform-specific type summary table
   - Add structure dependency graph

9. **Write `calltrace.md`** — from the draft:
   - Integrate the subagent's draft with module_design.json context
   - Ensure every flow has platform dependency classification
   - Ensure every module boundary crossing is marked
   - Include per-flow platform dependency summary tables

10. **Write per-module docs `NN-<module-name>.md`**:

    Each per-module doc must contain:
    - Module purpose and scope (1-2 paragraphs)
    - File inventory: each file with one-line purpose
    - Key data structures (with field descriptions, platform annotations)
    - API surface: public functions with signatures and brief descriptions
    - Internal flow (ASCII diagrams where helpful)
    - Dependencies: which modules this one uses and which depend on it
    - **Porting impact classification**: HIGH / MEDIUM / LOW / NONE with rationale

    **Batching strategy** (based on module count):
    - **≤8 modules**: Main agent writes all per-module docs directly.
    - **9-20 modules**: Spawn parallel foreground subagents, each handling a batch of 5-6 modules. Each subagent receives the full context needed to write quality docs independently.

    Per-module doc subagent prompt (substitute all `$VARIABLES` and `<BATCH_LIST>`):
    > You are a per-module documentation subagent. Your job is to write detailed per-module documentation files.
    >
    > Write docs for these modules: <BATCH_LIST, e.g. "modules 1-5: hip-transport, device-core, management, signaling-mlme, sap-layer">
    >
    > Inputs:
    > - Module design: `$OUTPUT_DIR/module_design.json` — read this for module definitions, file assignments, rationale, and cross-module edges
    > - Analysis data: `$OUTPUT_DIR/analysis.json` — read this for file inventory and include edges
    > - Architecture overview: `$OUTPUT_DIR/00-index.md` — read this for overall context, layer structure, and porting impact
    > - Data structures reference: `$OUTPUT_DIR/data-structures.md` — cross-reference key structures per module
    > - Workspace: `$WORKSPACE`
    > - Doxygen query script: `$DOXYGEN_DIR/scripts/query.py`
    >   Usage: `python3 $DOXYGEN_DIR/scripts/query.py $WORKSPACE <command> [args]`
    >   Use `file <path>` to get symbols defined in each file. Use `symbol <name>` for function details.
    >
    > For each module in your batch, write `$OUTPUT_DIR/NN-<module-name>.md` containing: module purpose (1-2 paragraphs), file inventory with one-line purposes, key data structures with platform annotations, API surface with function signatures, internal flow with ASCII diagrams where helpful, dependencies (uses/used-by), and porting impact classification (HIGH/MEDIUM/LOW/NONE with rationale).
    >
    > Use doxygen queries to verify function signatures and symbol locations — do not guess.

11. **Write `SKILL.md`** for the generated explainer skill:
    ```markdown
    # <Project> Codebase Explainer

    ## Triggers
    - explain <project> codebase
    - how does <project> work
    - <project> architecture
    - what does <module> do in <project>

    ## Module Map
    | Module | Files | Doc | Purpose | Porting Impact |
    |--------|-------|-----|---------|----------------|
    | ...    | ...   | ... | ...     | ...            |

    ## How to Use
    1. For overall architecture → read 00-index.md
    2. For data structures → read data-structures.md
    3. For execution flows → read calltrace.md
    4. For a specific module → find it in the table, read the linked doc
    5. For a specific function → use doxygen-generator's query.py

    ## Key Data Structures
    - `struct_name` — purpose (see data-structures.md)
    - ...

    ## Source
    - **Target workspace**: <absolute path to $WORKSPACE>
    - **Generated from**: codebase-explainer-generator
    - **Generated date**: <YYYY-MM-DD>
    ```

**Report progress**: Tell the user — "Phase 4 complete. {file_count} documentation files written." List each file with its size.

### Phase 5: Verification (parallel subagents, one per doc)

Spawn **one foreground Agent per documentation file** in parallel (subagent_type: general-purpose, do NOT use `run_in_background`), up to 5 at a time. Substitute all `$VARIABLES` with their resolved absolute paths:

> You are a verification subagent. Read the topic skill file at `$SKILL_DIR/topics/verification.md` for your full instructions.
>
> Document to verify: `$OUTPUT_DIR/<doc-file>.md`
> Workspace: `$WORKSPACE`
> Doxygen query script: `$DOXYGEN_DIR/scripts/query.py`
>   Usage: `python3 $DOXYGEN_DIR/scripts/query.py $WORKSPACE <command> [args]`
>
> Output: Write your correction report as JSON to `$OUTPUT_DIR/verification-<doc-file>.json`

**Wait for all to complete.** Read all verification JSON files.

**Report progress**: Tell the user — "Phase 5 complete. Verification results:" then show a table with columns: Document, Claims, Accuracy, Corrections.

### Phase 6: Fix & Finalize (main agent applies corrections)

12. **Review verification results**: For each doc, check the corrections:
    - Apply all HIGH-confidence corrections immediately
    - Apply MEDIUM-confidence corrections after reading the source to confirm
    - Skip LOW-confidence corrections (note them for the user)
    - Leave UNVERIFIABLE claims as-is (they are not wrong)

13. **Delete draft and verification files**:
    - Remove `*-draft.md` files
    - Remove `verification-*.json` files (their corrections are applied)

14. **Check file sizes**: Each doc must be under 100KB. If any exceeds this:
    - Split into sub-module docs (e.g., `02a-module-core.md`, `02b-module-transport.md`)

15. **Report results** to the user:
    - List all files created with sizes
    - Module count and total documentation size
    - Verification summary: claims checked, corrections applied, accuracy rate
    - Any modules that need manual review
    - Suggest next steps (e.g., "review 00-index.md for accuracy")

---

## Output Document Structure

```
$WORKSPACE/.claude/skills/<project>-explainer/
├── 00-index.md          # Architecture overview + platform dependency triage table
├── data-structures.md   # All key types with platform-specific field annotations
├── calltrace.md         # Execution flows with data mutations + platform calls
├── 01-<module>.md       # Per-module: files, APIs, platform impact classification
├── 02-<module>.md
├── ...
├── analysis.json        # Raw analysis data (truth from script)
├── module_design.json   # Module decomposition with rationale (from subagent)
└── SKILL.md             # Generated explainer skill definition
```

Key porting-agent features in every doc:
- Platform dependency columns in all tables
- Platform-specific type annotations on struct fields
- OS API calls described abstractly (what they do, not just the name)
- Per-module porting impact classification (HIGH/MEDIUM/LOW/NONE)

---

## Sizing Guidelines

| Codebase Size | Files | Modules to Doc | Output Files |
|---------------|-------|----------------|--------------|
| Tiny          | <20   | Skip per-module | 4 (index + data-structures + calltrace + SKILL.md) |
| Small         | 20-50 | 3-5 modules    | 7-9 |
| Medium        | 50-200| 5-12 modules   | 9-16 |
| Large         | 200-500| 10-20 modules | 14-24 |
| Very large    | 500+  | 15-20 max      | 19-24 |

---

## Agent Tips

- **Scripts produce truth, agents produce design** — never have a script make judgment calls about module boundaries or architecture.
- **Doxygen is required** — without symbol-level data, the data-structures and calltrace subagents cannot do their jobs. Always generate and verify the doxygen index in Phase 1.
- **Each subagent reads its topic file** — the topic files in `topics/` are the full instructions. The spawn prompt just points the subagent to the file and provides input paths.
- **Verification catches hallucinations** — always run Phase 5. The verification subagent compares claims against doxygen evidence, which is the only reliable way to catch fabricated symbol names or wrong call relationships.
- **Platform annotations are the primary value** — every table, every struct field, every function call should be annotated with platform dependency information. This is what makes the output useful for porting agents.
- **Cap each doc at 100KB** — split further if needed.
- **Prefer ASCII diagrams over prose** for data flows and layer architectures.
- **Cross-reference between docs** — use relative links like `[data structures](data-structures.md)`.
- **All file-writing subagents must run in foreground** — never use `run_in_background: true` for agents that write files. Background agents cannot prompt the user for Write permission and will silently fail. Only use background agents for read-only research.
