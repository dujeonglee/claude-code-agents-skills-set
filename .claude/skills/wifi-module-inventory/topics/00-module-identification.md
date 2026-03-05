# Topic 00 — Module Identification

You are a subagent performing **Step 2: Module Identification**.
Your input is the JSON scan data from `scan_source.py`.
Your output is a `module_boundaries.json` structure.

Also read: `topics/03-wifi-domain-knowledge.md` for file-name pattern hints.

---

## Your Task

Identify the logical module boundaries in the driver source tree.
Group every file into exactly one module. No file may be omitted.

---

## Step-by-Step Procedure

### Step 1: Read the Scan Data

Load the JSON report. Note:
- `file_inventory` — every .c and .h file with LOC
- `prefix_frequency` — function name prefix clusters (e.g., `scsc_wlan_tx_` x 12)
- `include_edges` — which files include which headers (local vs kernel)
- `struct_definitions` — structs defined per header file
- `makefile_targets` — obj-y groupings from Makefile

### Step 2: Apply Rule 1 — Single Responsibility First

For each candidate grouping, write one sentence starting with a verb that
describes the group's responsibility.

Test: If the sentence requires "and" to describe two unrelated things, the
group covers two modules and must be split.

Example GOOD responsibility:
> "Manages the 802.11 connection state machine (auth, assoc, deauth, roam)"

Example BAD responsibility (needs split):
> "Handles scanning and manages power-save states"

### Step 3: Apply Rule 2 — Cohesion as Evidence

Files belong together when they share **primary data structures** or form
a recognizable **function-name prefix cluster**.

Evidence types (strongest to weakest):
1. **Shared central struct** — files that read/write the same primary struct
2. **Function prefix cluster** — files whose functions share a common prefix
   (use `prefix_frequency` from scan data)
3. **Include chain** — files that include the same internal header
4. **Call relationship** — functions in one file call functions in another

### Step 4: Apply Rule 3 — File Name Patterns as Hints

Cross-reference file names against the pattern table in Topic 08.
These are starting hints only. Override with symbol evidence when they conflict.

### Step 5: Apply Rule 4 — Makefile as Tiebreaker

When a file matches no clear pattern and symbol evidence is ambiguous,
use the `makefile_targets` grouping to decide module membership.

### Step 6: Apply Rule 5 — Flag Ambiguous Files

If a file genuinely spans two responsibilities:
- Assign it to the **dominant** responsibility (where >60% of its functions belong)
- Add a `"flag": "mixed_responsibility"` annotation
- Record which secondary module it partially serves

---

## Output Format

Produce a JSON structure:

```json
{
  "modules": [
    {
      "name": "Module Name",
      "responsibility": "One sentence starting with a verb.",
      "files": [
        {"file": "example.c", "type": "core"},
        {"file": "example.h", "type": "internal-header"}
      ],
      "evidence": "Brief description of why these files belong together.",
      "flagged_files": [
        {"file": "mixed.c", "flag": "mixed_responsibility", "secondary_module": "Other Module"}
      ]
    }
  ],
  "unassigned_files": []
}
```

File `type` values: `core` | `interface` | `helper` | `internal-header` | `public-header`

The `unassigned_files` array must be empty when you are done. Every file
must appear in exactly one module.

---

## DO

- DO assign every file to exactly one module — verify the count matches `total_files`.
- DO write a responsibility sentence for every module that starts with a verb.
- DO use `prefix_frequency` data as primary evidence for grouping.
- DO cross-check your groupings against `include_edges` — files that include
  the same internal header likely belong together.
- DO flag files with `mixed_responsibility` rather than forcing them into one module.
- DO create a "Utility / Common" module for genuinely shared helper files.
- DO prefer fewer, larger modules over many tiny ones — aim for 5-15 modules per driver.

## DON'T

- DON'T leave any file unassigned — the `unassigned_files` array must be empty.
- DON'T create a module with only one file unless the file is genuinely standalone
  (e.g., a top-level init/exit file).
- DON'T group files together just because they are alphabetically adjacent.
- DON'T ignore the scan data and guess from file names alone — pattern hints
  are starting points, not conclusions.
- DON'T create modules whose responsibility requires "and" between unrelated concerns.
- DON'T put all headers into a single "Headers" module — headers belong with their
  corresponding source files.
- DON'T produce output that isn't valid JSON.
