# WiFi Driver Calltrace Analysis Skill

Analyzes kernel function call traces for WiFi driver stacks. Traces execution
flows across layers, identifies execution contexts, tracks lock relationships,
and explicitly links deferred/asynchronous execution paths.

---

## Triggers

Activate this skill when the user:
- Provides text containing structured call trace patterns:
  - Indented function chains with `→` or `└──` characters
  - ftrace-style `funcA() { funcB() }` notation
  - Numeric offset notation: `funcA+0x40/0x80`
- Asks to "analyze this call trace" or "trace the lock relationships"
- Asks to "explain the NAPI flow" or "map the TX/RX path"
- Asks "what context is this running in" for a kernel code path
- Asks to "find deadlock risks in this trace"
- Provides a Linux kernel stack trace (BUG / WARNING / WARN_ON output)

---

## Quick Start

```
User: Analyze this call trace:
  cfg80211_connect()
    └── drv_connect()
          └── driver_send_fw_cmd()
```

The skill will produce:
1. Mermaid flow diagram with context swim lanes
2. Function analysis table (7 columns)
3. Context transition summary
4. Lock dependency graph

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

### Phase 1: Input Parsing

Parse the user-provided trace text directly (no script needed).

1. **Detect format**: ftrace, dmesg, perf, or manual/pseudo (see Topic 04)
2. **Extract function list** in caller → callee order
3. **Determine analysis direction**:
   - Top-down: Kernel → Driver → HAL → Firmware (default)
   - Bottom-up: Firmware event → HAL → Driver → Kernel
   - Bidirectional: If the trace contains both directions
4. **Note any gaps**: If the trace has `...` or missing segments, flag them

**Verification after Phase 1:**
- All function names extracted (no offset notation remaining)
- Call order is caller → callee (dmesg traces reversed)
- Format identified

### Phase 2: Core Analysis

Launch a subagent to perform R1 through R4 analysis.

**Subagent instructions:**
- Read: `topics/04-calltrace-parsing.md` (R1 call order + R2 context tracking)
- Read: `topics/05-deferred-context.md` (R2-EXT deferred linkage — KEY)
- Read: `topics/06-locks-and-functions.md` (R3 locks + R4 per-function)
- Read: `topics/08-wifi-domain-knowledge.md` (domain reference)
- Input: The parsed function list from Phase 1, plus the original trace text
- Output: Annotated JSON with layers, contexts, lock analysis, function summaries,
  context transitions, and deferred linkage tags

**Critical check after Phase 2:**
- Every function has a layer and context annotation
- All deferred execution pairs have matching ORIGIN and BACK-REF tags
- Lock nesting order is recorded
- Function summaries are one-line each

### Phase 3: Output Generation

Launch a subagent to produce the four deliverables.

**Subagent instructions:**
- Read: `topics/07-calltrace-output-format.md` (O1–O4 format specification)
- Input: The annotated analysis JSON from Phase 2
- Output: Four Markdown sections (O1 diagram, O2 table, O3 transitions, O4 locks)

**Verification after Phase 3:**
- Mermaid diagram uses dashed arrows for deferred execution, solid for direct calls
- Function table has all functions from Phase 2 (count matches)
- Context transition table includes all deferred pairs
- Lock graph includes cycle detection results
- Tag IDs are consistent across O1, O2, and O3

### Phase 4: Assembly

Assemble the final output:

```markdown
# Calltrace Analysis: <brief description of the trace>

> Trace format: <detected format> | Functions: <count> | Direction: <top-down/bottom-up/bidirectional>

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

---

## Deferred Execution: The Key Requirement

The most critical aspect of this skill is **R2-EXT: Deferred Context Linkage**.
See `topics/05-deferred-context.md` for full details.

In brief: when a call trace triggers deferred execution (NAPI, workqueue, tasklet)
that runs in a separate trace, the two must be explicitly linked:

1. **TAG** the trigger site with an ORIGIN tag (e.g., `NAPI#RX`)
2. **TAG** the execution site with a BACK-REF to the same tag
3. **CONNECT** both in the diagram (dashed arrow), table (Notes column), and
   context transition summary

Unlinked deferred paths are the #1 failure mode for this skill.

---

## DO

- DO detect the trace format before parsing — don't assume a format.
- DO annotate every function with both layer and execution context.
- DO create Tag IDs for all deferred execution pairs.
- DO use dashed arrows in Mermaid diagrams for deferred execution.
- DO check lock ordering against the mandatory WiFi lock order (Topic 08).
- DO use subagents for Phases 2 and 3 — keep orchestration lightweight.
- DO present all four deliverables (O1–O4) in every analysis.

## DON'T

- DON'T skip deferred linkage — it is the KEY requirement.
- DON'T use solid arrows for deferred execution in diagrams.
- DON'T omit the context column in any output artifact.
- DON'T generate Module Inventory content — that is a separate skill.
- DON'T invent layer names not in the five-layer hierarchy (Topic 08).
- DON'T assume all functions run in the same context — check each one.
- DON'T skip the lock dependency graph even if "no deadlocks found" — report that.
