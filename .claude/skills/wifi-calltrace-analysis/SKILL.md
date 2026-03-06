# WiFi Driver Calltrace Analysis Skill

Analyzes call traces extracted from WiFi driver source code. Uses cscope to
build call chains from entry points, then traces execution flows across layers,
identifies execution contexts, tracks lock relationships, and explicitly links
deferred/asynchronous execution paths.

---

## Triggers

Activate this skill when the user:
- Provides a path to a WiFi driver source directory and asks for call trace analysis
- Asks to "analyze the call traces" or "trace the execution paths" for a driver
- Asks to "explain the NAPI flow" or "map the TX/RX path" for a source directory
- Asks "what context is this running in" for a kernel code path
- Asks to "find deadlock risks" in a driver source tree
- Provides a raw call trace text (ftrace, dmesg, perf, or manual/pseudo format)

---

## Quick Start

```
User: Analyze the call traces in /path/to/wifi/driver
```

The skill will:
1. Run `extract_calltrace.py` to extract call chains from all detected entry points
2. Analyze each entry point independently for layers, contexts, locks, and deferred linkage
3. Produce one document per entry point (four deliverables each) plus a summary index

For a specific entry point:
```
User: Analyze the call trace from slsi_connect in /path/to/wifi/driver
```

For raw trace text (legacy mode):
```
User: Analyze this call trace:
  cfg80211_connect()
    └── drv_connect()
          └── driver_send_fw_cmd()
```

---

## Output Deliverables

| ID | Deliverable | Description |
|----|-------------|-------------|
| O1 | Call Trace Flow Diagram | Mermaid sequenceDiagram with context swim lanes |
| O2 | Function Analysis Table | 7-column table: #, Function, Layer, Context, Locks, Responsibility, Notes |
| O3 | Context Transition Summary | All context transitions with trigger/execution pairs |
| O4 | Lock Dependency Graph | Directed graph of lock acquisition order with cycle detection |

---

## Workflow

### Output Directory

All intermediate and final output files are written to:
```
.claude/skills/wifi-calltrace-analysis/output/
```
This directory is created automatically. Using a workspace-relative path ensures
subagents have write permission without additional configuration.

**Output structure:**
```
output/
  calltrace_data.json              # Phase 1: extracted call chains (all entries)
  phase2a_<entry>.json             # Phase 2a: context analysis (per entry)
  phase2b_<entry>.json             # Phase 2b: lock analysis (per entry)
  phase3_<entry>.md                # Phase 3: raw deliverables (per entry)
  <entry>.md                       # Phase 4: final document (per entry)
  index.md                         # Phase 5: summary index with cross-entry analysis
```

### Phase 1: Data Collection

Determine the input type and extract call chain data.

**Input type A — Source directory (primary use case):**

Run the extraction script to build call chains from source code:

```bash
mkdir -p .claude/skills/wifi-calltrace-analysis/output
python3 scripts/extract_calltrace.py <driver_path> --auto-detect --output .claude/skills/wifi-calltrace-analysis/output/calltrace_data.json
```

To analyze specific entry points only:
```bash
python3 scripts/extract_calltrace.py <driver_path> --entry slsi_connect --entry slsi_scan --output .claude/skills/wifi-calltrace-analysis/output/calltrace_data.json
```

If the driver requires additional include paths or defines:
```bash
python3 scripts/extract_calltrace.py <driver_path> --auto-detect \
    -I /path/to/kernel/include -D CONFIG_SCSC_WLAN_ANDROID --output .claude/skills/wifi-calltrace-analysis/output/calltrace_data.json
```

The script:
- Preprocesses each `.c`/`.h` file with `clang -E` to resolve `#ifdef` blocks and macros
- Tokenizes the preprocessed output with a built-in C tokenizer
- Auto-detects entry points from `cfg80211_ops`, `netdev_ops`, `mac80211_ops` assignments, and NAPI/ISR registrations
- Extracts call chains using BFS traversal of caller→callee edges
- Detects deferred execution triggers (`napi_schedule`, `schedule_work`, etc.)
- Outputs structured JSON with nodes, edges, and deferred triggers per entry point

**Prerequisite:** `clang` must be installed (`xcode-select --install` on macOS / `apt install clang` on Linux).
Falls back to raw source parsing if clang is unavailable (less accurate with `#ifdef` blocks).

**Read the JSON output before proceeding.** All subsequent analysis uses this data.

**Input type B — Raw trace text (legacy):**

If the user provides raw trace text instead of a source directory, parse it directly:

1. **Detect format**:

   | Format          | Indicators                                                   |
   |-----------------|--------------------------------------------------------------|
   | ftrace          | `funcA() { funcB() { ... } }` or `function_graph` header    |
   | dmesg           | `[<timestamp>]` prefix, `Call Trace:` header, ` funcA+0x40` |
   | perf            | Numeric addresses, `cycles:`, indented call chains           |
   | manual/pseudo   | `→` arrows, `└──` tree characters, plain function names     |

2. **Extract function list** in caller → callee order
   - Strip offset notation: `funcA+0x40/0x80` → `funcA`
   - For dmesg stack traces, **reverse** the list (dmesg is callee-first)

**Verification after Phase 1:**
- For source directory: JSON contains entry_points and call_traces arrays
- For raw trace: All function names extracted, call order is caller → callee

### Phase 2a: Parsing & Context Analysis (R1 + R2 + R2-EXT)

Launch a subagent to perform call-order and context analysis.

**Subagent instructions:**
- Read: `topics/00-calltrace-parsing.md` (R1 call order + R2 context tracking)
- Read: `topics/01-deferred-context.md` (R2-EXT deferred linkage — KEY)
- Input: The call chain data from Phase 1 (JSON or parsed function list)
  - For source directory input: use one `call_traces` entry at a time
  - The script already identifies `deferred_triggers` — use these as starting points for R2-EXT
- Output: Write JSON to `.claude/skills/wifi-calltrace-analysis/output/phase2a_<entry_point>.json`
  containing `functions` array and `context_transitions` array (see schema in Topic 00)

**Verification after Phase 2a:**
- Every function has a layer and context annotation
- All deferred execution pairs have matching ORIGIN and BACK-REF tags

### Phase 2b: Lock & Per-Function Analysis (R3 + R4)

Launch a subagent to add lock analysis and function summaries.

**Subagent instructions:**
- Read: `topics/02-locks-and-functions.md` (R3 locks + R4 per-function)
- Read: `topics/04-wifi-domain-knowledge.md` (domain reference — lock ordering)
- Input: The Phase 2a JSON output, plus the original source files or trace text
  - For source directory input: the subagent may read specific `.c` files listed
    in the nodes to identify lock acquire/release patterns
- Output: Write JSON to `.claude/skills/wifi-calltrace-analysis/output/phase2b_<entry_point>.json`
  adding `locks`, `nesting_order`, `violations`, and
  `function_summaries` arrays (see schema in Topic 02)

**Verification after Phase 2b:**
- Lock nesting order is recorded
- Function summaries are one-line each
- Context-inappropriate lock usage is flagged

**Combined Phase 2 output** merges 2a and 2b into a single JSON:

```json
{
  "trace_format": "cscope|ftrace|dmesg|perf|manual",
  "entry_point": "slsi_connect",
  "functions": [ { "name": "...", "layer": "...", "context": "...", "depth": 0, "calls": [...], "layer_transition": "..." } ],
  "context_transitions": [ { "trigger_func": "...", "trigger_context": "...", "mechanism": "...", "exec_entry": "...", "exec_context": "...", "tag_id": "..." } ],
  "locks": [ { "name": "...", "type": "...", "acquire_func": "...", "release_func": "...", "scope": [...], "context": "...", "cross_context": false } ],
  "nesting_order": [ ["lock_a", "lock_b"] ],
  "violations": [ { "type": "...", "description": "...", "severity": "...", "functions": [...] } ],
  "function_summaries": [ { "name": "...", "summary": "...", "layer": "...", "anomalies": [...] } ]
}
```

### Phase 3: Output Generation

Launch a subagent to produce the four deliverables.

**Subagent instructions:**
- Read: `topics/03-calltrace-output-format.md` (O1–O4 format specification)
- Input: The combined Phase 2 JSON (merged output from Phase 2a + 2b)
- Output: Write four Markdown sections (O1 diagram, O2 table, O3 transitions, O4 locks)
  to `.claude/skills/wifi-calltrace-analysis/output/phase3_<entry_point>.md`

**Verification after Phase 3:**
- Mermaid diagram uses dashed arrows for deferred execution, solid for direct calls
- Function table has all functions from Phase 2 (count matches)
- Context transition table includes all deferred pairs
- Lock graph includes cycle detection results
- Tag IDs are consistent across O1, O2, and O3

### Phase 4: Per-Entry Assembly

For **each entry point**, assemble a standalone Markdown file and write it to:
```
.claude/skills/wifi-calltrace-analysis/output/<entry_point>.md
```

File content:

```markdown
# Calltrace Analysis: <entry_point>

> Source: <source directory or trace format> | Entry point: <function> | Functions: <count> | Direction: <top-down/bottom-up/bidirectional>

## Call Trace Flow Diagram
<O1 content>

## Function Analysis Table
<O2 content>

## Context Transition Summary
<O3 content>

## Lock Dependency Graph
<O4 content>
```

If anomalies were found, add a final section:

```markdown
## Anomalies & Warnings
- <anomaly description with affected functions>
```

**Repeat Phases 2–4 for every entry point.** After each entry, run validation.

### Phase 4b: Validation (per entry)

Run the validation script after each entry point's assembly to catch hallucination:

```bash
python3 scripts/validate_output.py .claude/skills/wifi-calltrace-analysis/output/calltrace_data.json <entry_point> \
    --output-dir .claude/skills/wifi-calltrace-analysis/output
```

The script checks Phase 2a, 2b, and final output against extraction ground truth:

| Check | What it catches | Severity |
|-------|----------------|----------|
| Invented functions | Function names not in extraction nodes | ERROR |
| Invented callees | Caller→callee edges not in extraction data | ERROR |
| Invalid layer names | Layer names outside the 5-layer hierarchy | ERROR |
| Invalid context names | Context values outside the allowed set | ERROR |
| Missing deferred triggers | Fewer transitions than extraction detected | ERROR |
| Invented trigger functions | Trigger function not in extraction nodes | ERROR |
| Invented violation functions | Lock violation referencing unknown function | ERROR |
| Table function names | Functions in Markdown table not in extraction | ERROR |
| Missing sections | Required Markdown sections (O1–O4) absent | ERROR |
| No dashed arrows | Deferred triggers exist but no `-->>` in diagram | ERROR |
| Missing functions | >50% of extraction functions absent | ERROR |
| Tag ID format | Tags not following `MECHANISM#PURPOSE` convention | WARN |
| Lock scope hallucination | Lock scope includes unknown function names | WARN |
| Summary count | Fewer summaries than extraction nodes | WARN |

**If errors are found:** Re-run the failed phase's subagent. The validation output
identifies exactly which checks failed and which data was hallucinated.

**To validate all entries at once** (after Phase 5):
```bash
python3 scripts/validate_output.py .claude/skills/wifi-calltrace-analysis/output/calltrace_data.json \
    --all --output-dir .claude/skills/wifi-calltrace-analysis/output
```

### Phase 5: Index Generation

After all per-entry documents are written, generate an index file at:
```
.claude/skills/wifi-calltrace-analysis/output/index.md
```

The index provides a summary table of all analyzed entry points with links
to their individual documents, plus a cross-entry lock ordering summary.

```markdown
# Calltrace Analysis Index: <driver name>

> Source: <source directory> | Entry points: <count> | Total functions: <count>

## Entry Point Summary

| # | Entry Point | Ops Table | Category | Functions | Deferred Triggers | Lock Violations | Link |
|---|-------------|-----------|----------|-----------|-------------------|-----------------|------|
| 1 | slsi_connect | cfg80211_ops | connect | 186 | 5 | 0 | [view](slsi_connect.md) |
| 2 | slsi_scan | cfg80211_ops | scan | 120 | 3 | 0 | [view](slsi_scan.md) |
| ... | | | | | | | |

## Cross-Entry Lock Ordering

Merge all per-entry lock nesting chains into a single global lock ordering graph.
Flag any **cross-entry ordering conflicts** — cases where entry A acquires lock X
before lock Y, but entry B acquires lock Y before lock X.

```mermaid
graph TD
    ...global lock ordering from all entries...
```

## Shared Function Overlap

List functions that appear in multiple entry point call traces, sorted by
frequency. These are the most critical shared code paths.

| Function | Appears In | Layer | Context(s) |
|----------|-----------|-------|------------|
| slsi_mlme_tx_rx | 38/46 entries | Firmware Interface | process |
| slsi_hip_transmit_frame | 35/46 entries | HW Abstraction | process |
| ... | | | |
```

**Verification after Phase 5:**
- Every entry point from Phase 1 has a corresponding `.md` file in the output dir
- The index table row count matches the number of analyzed entry points
- Cross-entry lock ordering conflicts (if any) are clearly flagged
- Shared function overlap table is sorted by frequency descending

---

## Deferred Execution: The Key Requirement

The most critical aspect of this skill is **R2-EXT: Deferred Context Linkage**.
See `topics/01-deferred-context.md` for full details.

When using source directory input, the extraction script already detects
`deferred_triggers` (calls to `napi_schedule`, `schedule_work`, etc.). Use these
as starting points for the full linkage analysis — the subagent must still:

1. **TAG** the trigger site with an ORIGIN tag (e.g., `NAPI#RX`)
2. **TAG** the execution site with a BACK-REF to the same tag
3. **CONNECT** both in the diagram (dashed arrow), table (Notes column), and
   context transition summary

Unlinked deferred paths are the #1 failure mode for this skill.

---

## DO

- DO run `extract_calltrace.py` first when given a source directory — never skip data collection.
- DO annotate every function with both layer and execution context.
- DO create Tag IDs for all deferred execution pairs.
- DO use dashed arrows in Mermaid diagrams for deferred execution.
- DO check lock ordering against the mandatory WiFi lock order (Topic 04).
- DO use subagents for Phases 2a, 2b, and 3 — keep orchestration lightweight.
- DO present all four deliverables (O1–O4) in every per-entry document.
- DO generate one document per entry point — never combine multiple entries into one file.
- DO generate the index (Phase 5) after all entry points are analyzed.
- DO run `validate_output.py` after each entry point to catch hallucination early.
- DO re-run failed phases when validation reports errors — do not skip validation.
- DO read source files when needed to resolve lock patterns that cscope edges alone cannot show.

## DON'T

- DON'T skip the extraction script and try to read source files directly for call chains.
- DON'T skip deferred linkage — it is the KEY requirement.
- DON'T use solid arrows for deferred execution in diagrams.
- DON'T omit the context column in any output artifact.
- DON'T generate Module Inventory content — that is a separate skill.
- DON'T invent layer names not in the five-layer hierarchy (Topic 04).
- DON'T assume all functions run in the same context — check each one.
- DON'T skip the lock dependency graph even if "no deadlocks found" — report that.
- DON'T combine multiple entry points into a single document — one file per entry.
- DON'T skip the index generation (Phase 5) — the cross-entry analysis catches global issues.
- DON'T skip validation (Phase 4b) — it is the primary defense against hallucination.
- DON'T ignore validation errors — re-run the failed subagent phase before proceeding.
