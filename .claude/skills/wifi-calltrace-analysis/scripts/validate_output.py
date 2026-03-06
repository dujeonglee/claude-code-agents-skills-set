#!/usr/bin/env python3
"""Validate subagent output against extraction ground truth.

Checks Phase 2a, 2b, and final output for hallucination by comparing
against the calltrace_data.json produced by extract_calltrace.py.

Usage:
    python3 validate_output.py <calltrace_data.json> <entry_point> [options]

Options:
    --phase2a <path>   Phase 2a JSON to validate (context analysis)
    --phase2b <path>   Phase 2b JSON to validate (lock analysis)
    --final <path>     Final Markdown to validate (assembled output)
    --all              Validate all entry points found in output dir
    --output-dir <dir> Output directory (default: ../output)
    --strict           Treat warnings as errors (exit code 1)

Exit codes:
    0  All checks passed
    1  Errors found (hallucination or missing data)
    2  Warnings only (non-strict mode)

Requirements:
    Python 3.8+
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


# =========================================================================
# Validation result tracking
# =========================================================================

class ValidationResult:
    """Tracks errors and warnings from validation checks."""

    def __init__(self, entry_point):
        self.entry_point = entry_point
        self.errors = []
        self.warnings = []
        self.info = []

    def error(self, check, msg):
        self.errors.append(f"[ERROR] {check}: {msg}")

    def warn(self, check, msg):
        self.warnings.append(f"[WARN]  {check}: {msg}")

    def ok(self, check, msg):
        self.info.append(f"[OK]    {check}: {msg}")

    def summary(self):
        lines = [f"\n=== Validation: {self.entry_point} ==="]
        for line in self.info:
            lines.append(line)
        for line in self.warnings:
            lines.append(line)
        for line in self.errors:
            lines.append(line)
        lines.append(f"--- {len(self.errors)} errors, {len(self.warnings)} warnings ---")
        return "\n".join(lines)

    @property
    def passed(self):
        return len(self.errors) == 0


# =========================================================================
# Load ground truth
# =========================================================================

def load_ground_truth(calltrace_path, entry_point):
    """Load extraction data for a specific entry point."""
    with open(calltrace_path) as f:
        data = json.load(f)

    for ct in data.get("call_traces", []):
        if ct.get("entry") == entry_point:
            return ct, data

    return None, data


def get_truth_sets(trace):
    """Extract ground truth sets from a call trace entry."""
    node_names = {n["name"] for n in trace["nodes"]}
    node_files = {n["name"]: n.get("file", "") for n in trace["nodes"]}
    node_lines = {n["name"]: n.get("line", 0) for n in trace["nodes"]}
    callee_map = {}
    for n in trace["nodes"]:
        callee_map[n["name"]] = set(n.get("callees", []))

    lock_ops_map = {}
    for n in trace["nodes"]:
        if n.get("lock_ops"):
            lock_ops_map[n["name"]] = n["lock_ops"]

    deferred_triggers = trace.get("deferred_triggers", [])
    trigger_funcs = {dt["trigger_func"] for dt in deferred_triggers}

    edges = set()
    for e in trace.get("edges", []):
        edges.add((e["caller"], e["callee"]))

    return {
        "node_names": node_names,
        "node_files": node_files,
        "node_lines": node_lines,
        "callee_map": callee_map,
        "lock_ops_map": lock_ops_map,
        "deferred_triggers": deferred_triggers,
        "trigger_funcs": trigger_funcs,
        "edges": edges,
        "node_count": trace.get("node_count", len(trace["nodes"])),
        "edge_count": trace.get("edge_count", len(trace["edges"])),
    }


# =========================================================================
# Known valid values
# =========================================================================

VALID_LAYERS = {
    "Kernel Standard Interface",
    "Driver Entry Points",
    "Driver Internal Logic",
    "HW Abstraction Layer",
    "Firmware Interface",
}

VALID_CONTEXTS = {
    "process",
    "softirq",
    "hardirq",
    "tasklet",
    "workqueue",
    "NAPI poll",
    "unknown",
}

VALID_LOCK_TYPES = {
    "mutex",
    "spinlock",
    "spinlock-irq",
    "spinlock-bh",
    "rtnl",
    "wiphy",
    "RCU",
    "rwlock",
    "semaphore",
}

VALID_MECHANISMS = {
    "NAPI", "WQ", "TASKLET", "NL", "TIMER",
}

VALID_VIOLATION_SEVERITIES = {
    "BUG", "DEADLOCK RISK", "DANGER", "WARNING",
}


# =========================================================================
# Phase 2a validation
# =========================================================================

def validate_phase2a(phase2a_path, truth, result):
    """Validate Phase 2a output (context analysis) against ground truth."""
    with open(phase2a_path) as f:
        p2a = json.load(f)

    functions = p2a.get("functions", [])
    transitions = p2a.get("context_transitions", [])

    # --- Check: entry_point matches ---
    if p2a.get("entry_point") != result.entry_point:
        result.error("entry_point", f"Expected '{result.entry_point}', got '{p2a.get('entry_point')}'")
    else:
        result.ok("entry_point", "matches")

    # --- Check: no invented function names ---
    p2a_names = {f["name"] for f in functions}
    invented = p2a_names - truth["node_names"]
    if invented:
        result.error("invented_functions",
                     f"{len(invented)} function(s) not in extraction data: {sorted(invented)[:10]}")
    else:
        result.ok("invented_functions", "none found")

    # --- Check: no missing functions (warn, not error — filtering is OK) ---
    missing = truth["node_names"] - p2a_names
    # Kernel leaf functions may be filtered out, that's acceptable
    non_leaf_missing = set()
    for n in missing:
        # Check if it's a kernel leaf in the trace
        for node in [nd for nd in functions if nd.get("name") == n]:
            pass  # won't match since it's missing
        # Look up in original nodes
        non_leaf_missing.add(n)

    if len(missing) > truth["node_count"] * 0.5:
        result.error("missing_functions",
                     f"{len(missing)}/{truth['node_count']} functions missing (>50%)")
    elif missing:
        result.warn("missing_functions",
                    f"{len(missing)}/{truth['node_count']} functions missing (may be filtered kernel leaves)")
    else:
        result.ok("missing_functions", "all functions present")

    # --- Check: valid layer names ---
    invalid_layers = set()
    for f in functions:
        layer = f.get("layer", "")
        if layer not in VALID_LAYERS:
            invalid_layers.add(layer)
    if invalid_layers:
        result.error("invalid_layers", f"Invented layer names: {invalid_layers}")
    else:
        result.ok("invalid_layers", "all layers valid")

    # --- Check: valid context names ---
    invalid_contexts = set()
    for f in functions:
        ctx = f.get("context", "")
        if ctx not in VALID_CONTEXTS:
            invalid_contexts.add(ctx)
    if invalid_contexts:
        result.error("invalid_contexts", f"Invented context names: {invalid_contexts}")
    else:
        result.ok("invalid_contexts", "all contexts valid")

    # --- Check: deferred trigger count matches ---
    p2a_trigger_count = len(transitions)
    truth_trigger_count = len(truth["deferred_triggers"])
    if p2a_trigger_count < truth_trigger_count:
        result.error("deferred_triggers",
                     f"Missing triggers: got {p2a_trigger_count}, expected {truth_trigger_count}")
    elif p2a_trigger_count > truth_trigger_count:
        result.warn("deferred_triggers",
                    f"Extra triggers: got {p2a_trigger_count}, expected {truth_trigger_count} "
                    "(may include inferred transitions)")
    else:
        result.ok("deferred_triggers", f"count matches ({truth_trigger_count})")

    # --- Check: trigger functions exist in truth ---
    for t in transitions:
        tf = t.get("trigger_func", "")
        if tf and tf not in truth["node_names"]:
            result.error("trigger_func_invented",
                         f"Trigger function '{tf}' not in extraction data")

    # --- Check: tag ID format ---
    for t in transitions:
        tag = t.get("tag_id", "")
        if tag and "#" not in tag:
            result.warn("tag_format", f"Tag '{tag}' doesn't follow MECHANISM#PURPOSE convention")
        elif tag:
            mechanism = tag.split("#")[0]
            if mechanism not in VALID_MECHANISMS:
                result.warn("tag_mechanism",
                            f"Tag mechanism '{mechanism}' not in known set {VALID_MECHANISMS}")

    # --- Check: callees match ground truth ---
    # A function's calls should be a subset of its callees from extraction.
    # Callees may include kernel leaf functions not in the node set — that's OK.
    # Only flag callees that are neither in node_names nor in the function's
    # known callee list from extraction.
    hallucinated_edges = []
    for f in functions:
        name = f.get("name", "")
        calls = set(f.get("calls", []))
        truth_calls = truth["callee_map"].get(name, set())
        # Invented = not a known callee AND not a known node
        invented_calls = calls - truth_calls - truth["node_names"]
        for c in invented_calls:
            hallucinated_edges.append(f"{name} -> {c}")
    if hallucinated_edges:
        result.error("hallucinated_edges",
                     f"{len(hallucinated_edges)} callee(s) not in extraction data: "
                     f"{hallucinated_edges[:5]}")
    else:
        result.ok("hallucinated_edges", "all callees verified")

    return result


# =========================================================================
# Phase 2b validation
# =========================================================================

def validate_phase2b(phase2b_path, truth, result):
    """Validate Phase 2b output (lock analysis) against ground truth."""
    with open(phase2b_path) as f:
        p2b = json.load(f)

    locks = p2b.get("locks", [])
    violations = p2b.get("violations", [])
    summaries = p2b.get("function_summaries", [])
    nesting = p2b.get("nesting_order", [])

    # --- Check: lock types are valid ---
    invalid_types = set()
    for lock in locks:
        lt = lock.get("type", "")
        if lt not in VALID_LOCK_TYPES:
            invalid_types.add(lt)
    if invalid_types:
        result.error("invalid_lock_types", f"Invented lock types: {invalid_types}")
    else:
        result.ok("invalid_lock_types", "all lock types valid")

    # --- Check: lock acquire/release functions exist ---
    for lock in locks:
        acq = lock.get("acquire_func", "")
        # acquire_func may list multiple functions separated by /
        for func in re.split(r'[/,]', acq):
            func = func.strip()
            if func and func not in truth["node_names"]:
                result.warn("lock_func_unknown",
                            f"Lock acquire func '{func}' not in extraction nodes")

    # --- Check: lock operations traced back to extraction data ---
    truth_lock_funcs = set(truth["lock_ops_map"].keys())
    claimed_lock_funcs = set()
    for lock in locks:
        for func in lock.get("scope", []):
            if isinstance(func, str):
                # Strip annotations like "(early check)"
                clean = func.split("(")[0].strip()
                if clean:
                    claimed_lock_funcs.add(clean)

    # Functions claiming locks but not in extraction nodes.
    # Filter out description strings (contain spaces or special chars).
    real_func_names = {f for f in claimed_lock_funcs
                       if re.match(r'^[a-zA-Z_]\w*$', f)}
    lock_hallucinations = real_func_names - truth["node_names"]
    if lock_hallucinations:
        result.warn("lock_scope_hallucination",
                    f"Lock scope includes unknown functions: {sorted(lock_hallucinations)[:5]}")

    # --- Check: violation severity values ---
    for v in violations:
        sev = v.get("severity", "")
        if sev not in VALID_VIOLATION_SEVERITIES:
            result.warn("invalid_severity", f"Unknown severity '{sev}'")

    # --- Check: violation functions exist ---
    for v in violations:
        for func in v.get("functions", []):
            if func not in truth["node_names"]:
                result.error("violation_func_invented",
                             f"Violation references unknown function '{func}'")

    # --- Check: function summary count ---
    summary_names = {s["name"] for s in summaries}
    if len(summaries) < truth["node_count"] * 0.5:
        result.error("summary_count",
                     f"Only {len(summaries)}/{truth['node_count']} summaries (<50%)")
    elif len(summaries) < truth["node_count"]:
        result.warn("summary_count",
                    f"{len(summaries)}/{truth['node_count']} summaries (some missing)")
    else:
        result.ok("summary_count", f"{len(summaries)} summaries")

    # --- Check: summary function names exist ---
    invented_summaries = summary_names - truth["node_names"]
    if invented_summaries:
        result.error("summary_names_invented",
                     f"{len(invented_summaries)} summary function(s) not in extraction: "
                     f"{sorted(invented_summaries)[:10]}")
    else:
        result.ok("summary_names_invented", "all summary names verified")

    # --- Check: summary layer values ---
    invalid_layers = set()
    for s in summaries:
        layer = s.get("layer", "")
        if layer not in VALID_LAYERS:
            invalid_layers.add(layer)
    if invalid_layers:
        result.error("summary_invalid_layers", f"Invented layer names in summaries: {invalid_layers}")
    else:
        result.ok("summary_invalid_layers", "all summary layers valid")

    # --- Check: nesting order uses real lock names (warn only, names may differ) ---
    if not nesting:
        result.warn("nesting_empty", "No lock nesting order recorded")
    else:
        result.ok("nesting_order", f"{len(nesting)} nesting chain(s)")

    return result


# =========================================================================
# Final Markdown validation
# =========================================================================

def validate_final(final_path, truth, result):
    """Validate final assembled Markdown against ground truth."""
    with open(final_path) as f:
        content = f.read()

    # --- Check: required sections present ---
    required_sections = [
        "Call Trace Flow Diagram",
        "Function Analysis Table",
        "Context Transition Summary",
        "Lock Dependency Graph",
    ]
    for section in required_sections:
        if section not in content:
            result.error("missing_section", f"Required section '{section}' not found")
        else:
            result.ok("section_present", f"'{section}' found")

    # --- Check: Mermaid diagram present ---
    if "```mermaid" not in content:
        result.error("no_mermaid", "No Mermaid diagram found")
    else:
        result.ok("mermaid_present", "Mermaid diagram found")

    # --- Check: dashed arrows for deferred execution ---
    if truth["deferred_triggers"]:
        if "-->>" not in content and "-- >>" not in content:
            result.error("no_dashed_arrows",
                         "Deferred triggers exist but no dashed arrows (-->>) in diagram")
        else:
            result.ok("dashed_arrows", "Dashed arrows present for deferred execution")

    # --- Check: function names in table exist in extraction ---
    # Extract function names from Markdown table rows
    table_funcs = set()
    for line in content.split("\n"):
        # Match table rows: | # | function_name | ...
        m = re.match(r'\|\s*\d+\s*\|\s*(\w+)\s*\|', line)
        if m:
            table_funcs.add(m.group(1))

    if table_funcs:
        invented_in_table = table_funcs - truth["node_names"]
        if invented_in_table:
            result.error("table_invented_funcs",
                         f"Table contains {len(invented_in_table)} unknown function(s): "
                         f"{sorted(invented_in_table)[:10]}")
        else:
            result.ok("table_func_names", f"all {len(table_funcs)} table functions verified")
    else:
        result.warn("no_table_funcs", "Could not parse function names from table")

    # --- Check: entry point mentioned ---
    if result.entry_point not in content:
        result.error("entry_point_missing",
                     f"Entry point '{result.entry_point}' not mentioned in output")
    else:
        result.ok("entry_point_mentioned", f"'{result.entry_point}' found in output")

    # --- Check: context column not empty ---
    context_found = False
    for ctx in VALID_CONTEXTS:
        if ctx in content:
            context_found = True
            break
    if not context_found:
        result.error("no_context_values", "No valid context values found in output")
    else:
        result.ok("context_values", "Context values present")

    # --- Check: tag IDs consistent across sections ---
    tag_pattern = re.compile(r'((?:NAPI|WQ|TASKLET|NL|TIMER)#\w+)')
    tags = tag_pattern.findall(content)
    if truth["deferred_triggers"] and not tags:
        result.error("no_tag_ids", "Deferred triggers exist but no tag IDs in output")
    elif tags:
        # Each tag should appear at least twice (diagram + table or transition)
        from collections import Counter
        tag_counts = Counter(tags)
        single_tags = [t for t, c in tag_counts.items() if c < 2]
        if single_tags:
            result.warn("single_tag_refs",
                        f"Tag(s) appear only once (should cross-reference): {single_tags}")
        else:
            result.ok("tag_cross_refs", f"All {len(tag_counts)} tag(s) cross-referenced")

    return result


# =========================================================================
# Index validation
# =========================================================================

def validate_index(index_path, calltrace_data, result_obj):
    """Validate index.md against all entry points."""
    result = ValidationResult("index")

    with open(index_path) as f:
        content = f.read()

    all_entries = {ct["entry"] for ct in calltrace_data.get("call_traces", [])}

    # --- Check: all entry points listed ---
    missing_entries = []
    for entry in all_entries:
        if entry not in content:
            missing_entries.append(entry)

    if missing_entries:
        result.error("missing_entries",
                     f"{len(missing_entries)}/{len(all_entries)} entry points missing from index: "
                     f"{sorted(missing_entries)[:10]}")
    else:
        result.ok("entry_coverage", f"All {len(all_entries)} entry points listed")

    # --- Check: required sections ---
    for section in ["Entry Point Summary", "Cross-Entry Lock Ordering", "Shared Function Overlap"]:
        if section not in content:
            result.error("missing_index_section", f"Required section '{section}' not found")
        else:
            result.ok("index_section", f"'{section}' found")

    # --- Check: links to per-entry files ---
    link_pattern = re.compile(r'\[view\]\((\w+)\.md\)')
    links = link_pattern.findall(content)
    if not links:
        result.warn("no_entry_links", "No [view](entry.md) links found in index")
    else:
        result.ok("entry_links", f"{len(links)} entry links found")

    return result


# =========================================================================
# Main
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Validate calltrace analysis output against extraction ground truth"
    )
    parser.add_argument("calltrace_data", help="Path to calltrace_data.json (ground truth)")
    parser.add_argument("entry_point", nargs="?", help="Entry point to validate")
    parser.add_argument("--phase2a", help="Phase 2a JSON path")
    parser.add_argument("--phase2b", help="Phase 2b JSON path")
    parser.add_argument("--final", help="Final Markdown path")
    parser.add_argument("--index", help="Index Markdown path")
    parser.add_argument("--all", action="store_true", help="Validate all entry points in output dir")
    parser.add_argument("--output-dir", help="Output directory to scan for files")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = parser.parse_args()

    # Load ground truth
    with open(args.calltrace_data) as f:
        calltrace_data = json.load(f)

    all_results = []

    if args.all and args.output_dir:
        # Validate all entry points found in output directory
        output_dir = Path(args.output_dir)
        for ct in calltrace_data.get("call_traces", []):
            entry = ct["entry"]
            result = ValidationResult(entry)
            truth = get_truth_sets(ct)

            p2a = output_dir / f"phase2a_{entry}.json"
            p2b = output_dir / f"phase2b_{entry}.json"
            final = output_dir / f"{entry}.md"

            if p2a.exists():
                validate_phase2a(str(p2a), truth, result)
            if p2b.exists():
                validate_phase2b(str(p2b), truth, result)
            if final.exists():
                validate_final(str(final), truth, result)

            if not p2a.exists() and not p2b.exists() and not final.exists():
                result.warn("no_output", f"No output files found for {entry}")

            all_results.append(result)

        # Validate index
        index_path = output_dir / "index.md"
        if index_path.exists():
            idx_result = validate_index(str(index_path), calltrace_data, None)
            all_results.append(idx_result)
        else:
            idx_result = ValidationResult("index")
            idx_result.warn("no_index", "index.md not found")
            all_results.append(idx_result)

    elif args.entry_point:
        # Validate single entry point
        trace, _ = load_ground_truth(args.calltrace_data, args.entry_point)
        if not trace:
            print(f"ERROR: Entry point '{args.entry_point}' not found in {args.calltrace_data}")
            sys.exit(1)

        truth = get_truth_sets(trace)
        result = ValidationResult(args.entry_point)

        if args.phase2a:
            validate_phase2a(args.phase2a, truth, result)
        if args.phase2b:
            validate_phase2b(args.phase2b, truth, result)
        if args.final:
            validate_final(args.final, truth, result)

        if not args.phase2a and not args.phase2b and not args.final:
            # Try to auto-detect from output-dir
            if args.output_dir:
                od = Path(args.output_dir)
                p2a = od / f"phase2a_{args.entry_point}.json"
                p2b = od / f"phase2b_{args.entry_point}.json"
                final = od / f"{args.entry_point}.md"
                if p2a.exists():
                    validate_phase2a(str(p2a), truth, result)
                if p2b.exists():
                    validate_phase2b(str(p2b), truth, result)
                if final.exists():
                    validate_final(str(final), truth, result)

        all_results.append(result)

        if args.index:
            idx_result = validate_index(args.index, calltrace_data, None)
            all_results.append(idx_result)

    else:
        print("ERROR: Provide --entry_point or --all with --output-dir")
        sys.exit(1)

    # Print results
    total_errors = 0
    total_warnings = 0
    for r in all_results:
        print(r.summary())
        total_errors += len(r.errors)
        total_warnings += len(r.warnings)

    print(f"\n{'='*50}")
    print(f"TOTAL: {total_errors} errors, {total_warnings} warnings across {len(all_results)} entries")

    if total_errors > 0:
        sys.exit(1)
    elif total_warnings > 0 and args.strict:
        sys.exit(1)
    elif total_warnings > 0:
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
