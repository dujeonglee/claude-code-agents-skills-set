# Topic 05 — Per-Variant Call Graph Analysis

You are a subagent producing per-variant call graph comparison output.
Your input is two or more `calltrace_data.json` files, each extracted with
a different set of kernel config defines (variants).
Your output is a Markdown document comparing the call graphs across variants.

---

## What Is a Variant?

A **variant** is a kernel configuration that selects different code paths
via `#ifdef CONFIG_*` blocks at compile time. The `clang -E` preprocessor
resolves these blocks, so each extraction run sees only ONE variant's code.

To analyze all variants, the extraction script must be run multiple times
with different `-D` flags. Each run produces a separate JSON file tagged
with a `--variant` name.

### Example

A driver uses `CONFIG_FEATURE_X` to switch between two implementations.
When `CONFIG_FEATURE_X` is defined:
- `impl_x.c` is compiled (`impl_y.c` is excluded)
- `#ifdef CONFIG_FEATURE_X` blocks are active
- Entry points like `impl_x_poll` exist; `impl_y_handler` does not

When `CONFIG_FEATURE_X` is NOT defined:
- `impl_y.c` is compiled (`impl_x.c` is excluded)
- `#else` branches are active
- Entry points like `impl_y_handler` exist; `impl_x_poll` does not

---

## How to Identify Variants

Use the `--detect-variants` flag to automatically scan for variants:

```bash
python3 scripts/extract_calltrace.py <driver_path> --detect-variants [--min-lines N]
```

### Three-Tier Detection

The script detects variants using three tiers, from highest to lowest impact:

#### Tier 1: Makefile Source File Switches (highest impact)

Configs in `ifeq/else` blocks that switch `.o` file targets:

```makefile
ifeq ($(CONFIG_FEATURE_X),y)
module-$(CONFIG_MODULE) += impl_x.o
else
module-$(CONFIG_MODULE) += impl_y.o
endif
```

These swap entire source files — the most significant variant type.

#### Tier 2: Makefile Compile-Time Defines (medium impact)

Configs passed as `-D` flags via `ccflags`:

```makefile
ccflags-$(CONFIG_FEATURE_X) += -DCONFIG_FEATURE_X
ccflags-y += -DCONFIG_FEATURE_Y
```

These activate `#ifdef` blocks within shared source files.

#### Tier 3: Source `#ifdef` Guarded Blocks (variable impact)

`#ifdef CONFIG_*` blocks in source files with guarded line count above a
threshold (default: 50 lines, configurable with `--min-lines`). Sorted by
guarded line count descending.

### Grouping and Dependencies

- **Kconfig `select` dependencies** are resolved automatically. If
  `CONFIG_FEATURE_X` selects `CONFIG_DEPENDENCY_A`, the variant's define
  set includes both.
- **Tier 2/3 configs implied by Tier 1** are folded into the Tier 1 variant
  (not listed separately).

### Output

The script outputs JSON with all detected variants grouped by tier.
Present the results to the user and let them select which variants to
extract. Typically Tier 1 variants are the most important.

```
Discovered config variants:
  [Tier 1] variant_a: -D CONFIG_FEATURE_X -D CONFIG_DEPENDENCY_A
           set: impl_x.o | unset: impl_y.o
  [Tier 1] variant_b: (baseline — no extra defines)
  [Tier 2] variant_c: -D CONFIG_FEATURE_Y
  ...
Proceed? [Y/n]
```

---

## Extraction Commands

Run `extract_calltrace.py` once per variant:

```bash
# Variant A
python3 scripts/extract_calltrace.py <driver_path> --auto-detect \
    -D CONFIG_X -D CONFIG_Y \
    --variant variant_a \
    --output .claude/skills/wifi-calltrace-analysis/output/calltrace_data_variant_a.json

# Variant B (baseline)
python3 scripts/extract_calltrace.py <driver_path> --auto-detect \
    --variant variant_b \
    --output .claude/skills/wifi-calltrace-analysis/output/calltrace_data_variant_b.json
```

---

## Comparison Analysis

### Step 1: Entry Point Diff

Compare the entry point lists across variants:

| Entry Point | Variant A | Variant B | Notes |
|-------------|-----------|-----------|-------|
| func_a_poll | YES | - | Only in variant A |
| func_b_handler | - | YES | Only in variant B |
| shared_connect | YES | YES | Shared entry point |

### Step 2: Per-Entry Call Graph Diff (shared entries only)

For entry points that appear in **both** variants, compare their call
graphs:

1. **Nodes diff**: Functions present in one variant but not the other
2. **Edges diff**: Call edges that differ between variants
3. **Deferred triggers diff**: Deferred execution paths that differ

Present as a table:

| Function | Variant A | Variant B | Diff Type |
|----------|-----------|-----------|-----------|
| func_x | YES (depth 3) | - | variant-A-only node |
| func_y | - | YES (depth 2) | variant-B-only node |
| func_z | calls func_p | calls func_q | different callee |

### Step 3: Variant-Exclusive Entry Points

For entry points that exist in only one variant, produce their full
call graph analysis (O1–O4) as usual, but clearly label them with
their variant.

---

## Output Format: O5 — Per-Variant Call Graph Comparison

### Section Header

```markdown
## Per-Variant Call Graph Comparison
```

### Sub-sections

#### 1. Variant Configuration Summary

```markdown
### Variant Configuration

| Variant | Defines | Entry Points | Total Functions |
|---------|---------|-------------|-----------------|
| variant_a | CONFIG_X, CONFIG_Y | 48 | 2100 |
| variant_b | (baseline) | 44 | 1850 |
```

#### 2. Entry Point Availability Matrix

```markdown
### Entry Point Availability

| Entry Point | Ops Table | variant_a | variant_b |
|-------------|-----------|-----------|-----------|
| func_a_poll | napi | YES | - |
| func_b_handler | interrupt | - | YES |
| shared_connect | cfg80211_ops | YES | YES |
```

#### 3. Shared Entry Point Diffs

For each shared entry point with differences, show:

```markdown
### Call Graph Diff: shared_connect

**Summary**: variant_a has 195 nodes, variant_b has 180 nodes.
15 nodes are variant-a-only, 0 nodes are variant-b-only.

#### Variant-A-Only Functions
| Function | File | Depth | Called By |
|----------|------|-------|----------|
| func_x | file.c | 3 | parent_func |

#### Variant-B-Only Functions
| Function | File | Depth | Called By |
|----------|------|-------|----------|
| (none) | | | |

#### Different Call Edges
| Caller | Variant A Callee | Variant B Callee |
|--------|-----------------|-----------------|
| shared_func | variant_a_impl | variant_b_impl |
```

#### 4. Variant-Exclusive Call Graphs

```markdown
### Variant-Exclusive Entry Points

#### variant_a only
- [func_a_poll](func_a_poll.md) — NAPI poll handler for variant A

#### variant_b only
- [func_b_handler](func_b_handler.md) — IRQ handler for variant B
```

---

## DO

- DO use `--detect-variants` to discover variant-defining configs automatically.
- DO include implied configs (from `select` statements) in each variant's define set.
- DO run extraction separately for each variant — never mix defines from different variants.
- DO compare shared entry points at the node and edge level.
- DO clearly label variant-exclusive entry points and their documents.
- DO present the variant discovery to the user for confirmation before extraction.

## DON'T

- DON'T guess which configs define variants — use `--detect-variants`.
- DON'T skip the baseline variant — always include a minimal-config extraction.
- DON'T mix variant-exclusive functions into shared entry point analysis.
- DON'T produce per-variant comparison for entry points that are identical across variants.
- DON'T invent config names — only use configs found in the driver's `Kconfig` and `Makefile`.
