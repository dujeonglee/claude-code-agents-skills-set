# Feedback: codebase-explainer-generator Test Run #3 (2026-03-03)

**Target codebase**: pcie_scsc (263 files, 191,476 LOC, C/Linux kernel module)
**Output location**: `pcie_scsc/.claude/skills/pcie_scsc-explainer-1.1.0/`
**Generator version**: 1.1.0

---

## Test Results Summary

| Metric | Run #2 (v1.0.0) | Run #3 (v1.1.0, this) | Delta |
|--------|-----------------|----------------------|-------|
| Total output files | 20 | 28 | +8 (subtopic splits + more modules) |
| Total output lines | ~6,100 | 7,458 | +1,358 |
| Total output size | ~550 KB | 652 KB | +102 KB |
| Modules detected | 17 | 19 | +2 (debug-utilities, connection-features split out) |
| Layers | 8 | 7 | -1 (consolidated) |
| Cross-module edges | 29 | 36 | +7 (finer modules = more edges) |
| 00-index.md accuracy | 95% (93/98) | 91% (60/68) | -4pp |
| data-structures.md accuracy | 98% (184/187) | 98.7% (294/300) | +0.7pp |
| calltrace.md accuracy | 94% (84/89) | 94% (134/142) | 0pp |
| Overall accuracy | 96% (374 claims) | 95.7% (510 claims) | -0.3pp |
| Major corrections | 1 | 5 | +4 |
| Minor corrections | 6 | 9 | +3 |
| Cosmetic corrections | 1 | 1 | 0 |
| Total corrections | 8 | 15 | +7 |

---

## Phase-by-Phase Observations

### Phase 1: Data Collection
- Identical to Run #2: 263 files, 988 include edges, 7 variants, 12,946 doxygen symbols
- v1.1.0 skill already deployed at pcie_scsc/.claude/skills/codebase-explainer-generator/

### Phase 2: Module Design
- **19 modules** (vs 17 in Run #2): debug-utilities and connection-features emerged as separate modules. `nl80211-vendor` re-merged into cfg80211-interface.
- **7 layers** (vs 8): slightly coarser layer grouping
- **36 cross-module edges** (vs 29): finer module granularity means more boundary crossings
- **0 unassigned files**
- The removal of the hard 5-20 module count limit had no negative effect — the subagent naturally chose 19 modules based on cohesion analysis
- Cohesion-based guideline worked: the subagent documented rationale for each module citing include clusters, build targets, and functional cohesion. No "god modules" appeared.
- Complexity bounds respected: wifilogger (23 files) and kunit-tests (117 files) have documented justification

### Phase 3: Topic Analysis
- **Data structures**: 25 structures, 777 lines (vs 27 structures, 866 lines in Run #2 — slightly tighter)
- **Calltrace**: 6 flows, 678 lines (vs 6 flows, 724 lines)
- **Domain-aware flow selection (v1.1.0 change)**: Calltrace subagent explicitly identified pcie_scsc as a "Samsung SCSC PCIe WiFi kernel driver" in Step 1a, then derived domain-specific flows: init, bring-up, connection, TX data, RX data, teardown. This is effectively the same as Run #2's flows (the WiFi domain naturally maps to these), but the reasoning is now explicit and principled rather than falling through a generic checklist.
- **Lifecycle verification mandate (v1.1.0 change)**: Data-structures subagent reported verifying all lifecycle functions via doxygen. The `mbulk_pool_alloc()` fabrication from Run #1 and #2 did NOT recur. However, 2 new lifecycle errors appeared (see I1 below).

### Phase 4: Documentation Writing
- **00-index.md**: 270 lines (vs 408 in Run #2 — more focused), all 11 required sections present
- **Subtopic splitting (v1.1.0 change)**: cfg80211-interface split into 3 files (80+265+240=585 lines total), wifilogger split into 2 files (189+309=498 lines). All per-module docs stayed under 400 lines. This is a concrete improvement — no monolithic 400+ line single files.
- **Per-module docs**: 19 production modules across 23 files (4 per-module doc subagent batches ran in parallel)
- **Verbatim flow copy (v1.1.0 change)**: The CRITICAL instruction in section 7g was followed — 00-index.md flow summaries match calltrace.md module attributions exactly (see I1 fix verification below).
- **Generated SKILL.md**: 204 lines with full U1-U7 inlined, verification summary populated

### Phase 5: Verification
- 3 verification subagents on core docs (same scope as Run #2)
- **00-index.md**: 91% (2 major + 4 minor corrections)
- **data-structures.md**: 98.7% (2 major + 2 minor corrections)
- **calltrace.md**: 94% (1 major + 3 minor + 1 cosmetic corrections)
- More claims checked (510 vs 374) due to more thorough verification

### Phase 6: Fix & Finalize
- Applied 15 corrections (5 major, 9 minor, 1 cosmetic)
- Updated SKILL.md verification summary
- Cleaned up draft files and verification JSONs
- All files under 400 lines (largest: 01-hip-subsystem.md at 396 lines)

---

## v1.1.0 Change Impact Assessment

### Change 1: Remove hard module count limit — POSITIVE
The subagent chose 19 modules without an arbitrary cap, producing finer-grained and more cohesive modules. No "module explosion" occurred. The rationale-based check (each module needs documented evidence) provides sufficient guardrails.

### Change 2: Cohesion-based module definition — POSITIVE
Module rationales now cite include-cluster density, functional decomposition, and cross-module edge minimization. The subagent's output reads as principled analysis rather than mechanical build-target mapping. The 2 new modules (debug-utilities, connection-features split from prior groupings) are well-justified.

### Change 3: Multi-file per-module docs — POSITIVE
cfg80211-interface (459 target lines) correctly split into overview/api/internals. wifilogger (482 target lines) correctly split into overview/internals. All other modules stayed as single files under 400 lines. File sizes are LLM-context-friendly (~10KB max). This is a tangible structural improvement.

### Change 4: Domain-aware flow selection — NEUTRAL
For a WiFi driver, the domain-specific flows (connection, TX, RX, power management) map naturally to the old generic categories (init, data path, teardown). The subagent's reasoning was more explicit but the output flows are equivalent. This change will likely show more value on non-network-driver codebases where the generic categories are a poor fit.

### Change 5: Fix I1 (verbatim flow copy) — CONFIRMED FIXED
The recurring RX flow module misattribution bug from Run #1 and #2 is **eliminated**. In 00-index.md section g, `slsi_rx_queue_data()`, `slsi_rx_netdev_data_work()`, `slsi_rx_data_ind()`, and `slsi_rx_data_deliver_skb()` are all correctly labeled [sap-layer], matching calltrace.md exactly. The CRITICAL instruction worked.

### Change 6: Fix I2 (lifecycle function verification) — PARTIALLY FIXED
The specific `mbulk_pool_alloc()` fabrication that recurred in Run #1 and #2 is **gone**. The data-structures subagent used correct lifecycle functions for mbulk (`mbulk_with_signal_alloc`, `mbulk_seg_free`). However, 2 new lifecycle errors appeared (`slsi_netif_add()` should be `slsi_netif_add_locked()`, `slsi_rx_ba_stop()` should be `slsi_rx_ba_stop_all()`). The verification subagent correctly caught these as lifecycle function claims (claim type 7). The two-pronged defense works: even when the source subagent misses a verification, the downstream verification subagent catches it.

---

## Issues Found

### I1: Lifecycle Function Name Errors (Severity: MAJOR, Status: FIXED)
data-structures.md used `slsi_netif_add()` (correct: `slsi_netif_add_locked()`) and `slsi_rx_ba_stop()` (correct: `slsi_rx_ba_stop_all()` or `__slsi_rx_ba_stop()`).

**Root cause**: The data-structures subagent claimed to verify all lifecycle functions but missed these 2. The functions have similar names to real symbols, making them plausible-sounding. The subagent may have searched for a partial match and accepted it without exact verification.

**Mitigation**: The v1.1.0 verification subagent correctly caught both via claim type 7 ("lifecycle functions"). The two-pronged approach (prevent at source + catch at verification) works.

**Recommendation**: Strengthen the mandate: "Run `symbol <exact_function_name>` and confirm the result matches exactly. A partial match or similar-sounding name is NOT sufficient."

### I2: Module Attribution Error in Calltrace (Severity: MAJOR, Status: FIXED)
calltrace.md Flow 3 attributed `slsi_add_probe_ies_request()` to cfg80211-interface, but it's defined in `mgt.c` (management-engine module).

**Root cause**: The calltrace subagent assumed the function was in cfg80211-interface because it's called from `slsi_connect()` which is in cfg80211_ops.c. It didn't cross-reference the function's actual file against module_design.json.

**Recommendation**: Add to calltrace topic: "For every function, verify its file location via doxygen `symbol` query and look up the file's module assignment in module_design.json."

### I3: 00-index.md Field Count Inaccuracies (Severity: MINOR, Status: FIXED)
`slsi_hip` listed as 10 fields (actual: 8), `sap_api` as 5 fields (actual: 6), `load_manager` as ~10 fields (actual: 8).

**Root cause**: The 00-index.md writer estimated field counts from memory rather than querying doxygen members. These are the same class of error as Run #2's I6 (hip_priv field count).

### I4: ASCII Diagram File Name Typos (Severity: MINOR, Status: FIXED)
Layer diagram used `ini_conf.c` (correct: `ini_config.c`) and `slsi_cpuhp.c` (correct: `slsi_cpuhp_monitor.c`). The module map table had the correct names, showing the diagram was written independently.

### I5: Missing DPD mmap Call in Init Flow (Severity: MINOR, Status: NOT FIXED)
Same as Run #2 I3: calltrace Flow 1 omits `slsi_wlan_dpd_mmap_create()`. The conditional callee behind CONFIG_SCSC_WLAN_HOST_DPD is still missed by the calltrace subagent.

---

## Key Improvements Over Run #2

1. **I1 (flow misattribution) eliminated**: The CRITICAL verbatim copy instruction completely fixed the recurring module misattribution in 00-index.md flow summaries.
2. **I2 (mbulk_pool_alloc) eliminated**: The specific recurring lifecycle fabrication from Run #1 and #2 is gone. The lifecycle verification mandate works for known-bad patterns.
3. **Subtopic splitting works**: 2 modules correctly split into multi-file docs, all under 400 lines. No monolithic files.
4. **Finer module granularity**: 19 modules (vs 17) with principled cohesion-based reasoning. Removal of hard count limit had no negative effect.
5. **More comprehensive verification**: 510 claims checked (vs 374), catching more errors despite similar accuracy.
6. **Two-pronged lifecycle defense**: Even when the data-structures subagent misses a lifecycle error, the verification subagent catches it via claim type 7.

## Regressions

1. **More corrections needed (15 vs 8)**: Driven by more thorough verification (510 vs 374 claims) and 2 new lifecycle function errors. Not a quality regression per se — the errors were caught and fixed.
2. **00-index.md accuracy dropped (91% vs 95%)**: Due to field count inaccuracies and ASCII diagram typos. These are cosmetic issues in a summary doc, not architectural errors.
3. **Conditional callee omission persists**: Same `slsi_wlan_dpd_mmap_create()` miss as Run #2.

## Remaining Systematic Issues

1. **Lifecycle function name similarity**: Subagent accepts similar-sounding names (e.g., `slsi_netif_add` vs `slsi_netif_add_locked`). Need stricter exact-match verification.
2. **Calltrace module attribution**: Subagent assumes module from calling context rather than verifying file-to-module mapping. Need explicit cross-reference instruction.
3. **Conditional callees**: `#ifdef`-guarded callees in doxygen callgraphs are still systematically missed.
4. **Field count estimation**: Summary tables estimate field counts rather than querying doxygen. Should mandate `members` query.

## Recommended Next Actions

1. **Strengthen lifecycle verification wording**: Change "verify it exists" to "run `symbol <exact_name>`, confirm the result matches the exact function name. Do not accept partial matches."
2. **Add calltrace module cross-reference mandate**: "For every function in a call trace, query `symbol <name>` to get its file, then look up the file's module in module_design.json."
3. **Add 00-index field count mandate**: "For Key Data Structures table field counts, run `members <struct_name>` and use the actual count."
4. **Consider conditional callee instruction**: Add to calltrace: "For init/teardown flows, also query `callgraph <entry_point> --depth 4` and check for callees in conditionally-compiled files."
5. **Test on a non-C codebase**: Validate language generality on Go/Rust/Python project.
