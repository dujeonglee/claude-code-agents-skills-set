#!/usr/bin/env python3
"""Extract call traces from C source code using clang preprocessing + C tokenizer.

Preprocesses C source files with clang -E (resolves #ifdef, macros), then
tokenizes the output to extract function definitions, call edges, and
ops-table assignments (cfg80211_ops, netdev_ops, etc.), then builds call
chains from detected entry points.

Usage:
    python3 extract_calltrace.py <source_dir> [options]

Options:
    --output <path>       Output JSON file (default: stdout)
    --entry <func>        Entry point function name (repeatable)
    --auto-detect         Auto-detect entry points from ops tables and NAPI/ISR
    --max-depth <N>       Maximum call chain depth (default: unlimited)
    --exclude-prefix <p>  Exclude functions matching prefix (repeatable)
    --include <path>      Additional include path for clang -E (repeatable)
    --define <macro>      Additional macro definition for clang -E (repeatable)

Output:
    JSON with call traces rooted at each entry point.

Requirements:
    Python 3.8+, clang (for preprocessing)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from enum import Enum, auto
from pathlib import Path


# =========================================================================
# Part 1: C Tokenizer
# =========================================================================

class TokType(Enum):
    IDENT = auto()
    NUMBER = auto()
    STRING = auto()
    CHAR = auto()
    PUNCT = auto()       # single/multi-char punctuation: { } ( ) ; , . -> = * &
    PREPROC = auto()     # entire preprocessor line: #include ... / #define ...
    EOF = auto()


class Token:
    __slots__ = ("type", "value", "line", "col")

    def __init__(self, type_, value, line, col):
        self.type = type_
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, L{self.line})"


def tokenize_c(source, filename="<input>"):
    """Tokenize C source code.  Yields Token objects.

    Properly skips:
      - Block comments   /* ... */
      - Line comments     // ...
      - String literals   "..."  (with escapes)
      - Char literals     '...'  (with escapes)

    Preprocessor lines (#...) are yielded as single PREPROC tokens.
    """
    i = 0
    n = len(source)
    line = 1
    col = 1

    # Multi-char punctuation (order matters: longest first)
    MULTI_PUNCT = ("->", "<<", ">>", "<=", ">=", "==", "!=", "&&", "||",
                   "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
                   "<<=", ">>=", "++", "--")

    SINGLE_PUNCT = set("{}()[];,.*&~!+-/%<>=|^?:#")

    while i < n:
        c = source[i]

        # --- Newline ---
        if c == '\n':
            line += 1
            col = 1
            i += 1
            continue

        # --- Whitespace ---
        if c in ' \t\r\f\v':
            i += 1
            col += 1
            continue

        # --- Block comment ---
        if c == '/' and i + 1 < n and source[i + 1] == '*':
            i += 2
            col += 2
            while i < n:
                if source[i] == '\n':
                    line += 1
                    col = 1
                    i += 1
                elif source[i] == '*' and i + 1 < n and source[i + 1] == '/':
                    i += 2
                    col += 2
                    break
                else:
                    i += 1
                    col += 1
            continue

        # --- Line comment ---
        if c == '/' and i + 1 < n and source[i + 1] == '/':
            while i < n and source[i] != '\n':
                i += 1
            continue

        # --- Preprocessor line ---
        if c == '#' and (col == 1 or source[i - 1] in '\n\r\t '):
            start = i
            start_line = line
            start_col = col
            while i < n and source[i] != '\n':
                # Handle line continuation
                if source[i] == '\\' and i + 1 < n and source[i + 1] == '\n':
                    i += 2
                    line += 1
                    col = 1
                else:
                    i += 1
                    col += 1
            yield Token(TokType.PREPROC, source[start:i].strip(), start_line, start_col)
            continue

        # --- String literal ---
        if c == '"':
            start = i
            start_line = line
            start_col = col
            i += 1
            col += 1
            while i < n and source[i] != '"':
                if source[i] == '\\' and i + 1 < n:
                    i += 2
                    col += 2
                elif source[i] == '\n':
                    line += 1
                    col = 1
                    i += 1
                else:
                    i += 1
                    col += 1
            if i < n:
                i += 1  # skip closing "
                col += 1
            yield Token(TokType.STRING, source[start:i], start_line, start_col)
            continue

        # --- Char literal ---
        if c == "'":
            start = i
            start_line = line
            start_col = col
            i += 1
            col += 1
            while i < n and source[i] != "'":
                if source[i] == '\\' and i + 1 < n:
                    i += 2
                    col += 2
                else:
                    i += 1
                    col += 1
            if i < n:
                i += 1
                col += 1
            yield Token(TokType.CHAR, source[start:i], start_line, start_col)
            continue

        # --- Number ---
        if c.isdigit() or (c == '.' and i + 1 < n and source[i + 1].isdigit()):
            start = i
            start_col = col
            # Hex
            if c == '0' and i + 1 < n and source[i + 1] in 'xX':
                i += 2
                col += 2
                while i < n and (source[i].isalnum() or source[i] == '_'):
                    i += 1
                    col += 1
            else:
                while i < n and (source[i].isalnum() or source[i] in '._'):
                    i += 1
                    col += 1
            # Suffixes like UL, ULL, etc.
            while i < n and source[i] in 'uUlLfF':
                i += 1
                col += 1
            yield Token(TokType.NUMBER, source[start:i], line, start_col)
            continue

        # --- Identifier or keyword ---
        if c.isalpha() or c == '_':
            start = i
            start_col = col
            while i < n and (source[i].isalnum() or source[i] == '_'):
                i += 1
                col += 1
            yield Token(TokType.IDENT, source[start:i], line, start_col)
            continue

        # --- Multi-char punctuation ---
        matched = False
        for mp in MULTI_PUNCT:
            if source[i:i + len(mp)] == mp:
                yield Token(TokType.PUNCT, mp, line, col)
                i += len(mp)
                col += len(mp)
                matched = True
                break
        if matched:
            continue

        # --- Single-char punctuation ---
        if c in SINGLE_PUNCT:
            yield Token(TokType.PUNCT, c, line, col)
            i += 1
            col += 1
            continue

        # --- Unknown char: skip ---
        i += 1
        col += 1

    yield Token(TokType.EOF, "", line, col)


# =========================================================================
# Part 2: Function Definition & Call Extraction
# =========================================================================

# C keywords that look like identifiers but aren't function names
C_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if",
    "inline", "int", "long", "register", "restrict", "return", "short",
    "signed", "sizeof", "static", "struct", "switch", "typedef", "typeof",
    "union", "unsigned", "void", "volatile", "while",
    "_Atomic", "_Bool", "_Complex", "_Noreturn", "_Static_assert",
    "__attribute__", "__extension__", "__typeof__", "__inline__",
}

# Known noise: logging macros, assertions, compiler builtins
NOISE_PATTERNS = {
    "SLSI_NET_DBG", "SLSI_NET_WARN", "SLSI_NET_ERR", "SLSI_NET_INFO",
    "SLSI_DBG", "SLSI_WARN", "SLSI_ERR", "SLSI_INFO",
    "WARN_ON", "BUG_ON", "WARN", "BUG",
    "pr_err", "pr_warn", "pr_info", "pr_debug",
    "dev_err", "dev_warn", "dev_info", "dev_dbg",
    "printk", "netdev_err", "netdev_warn", "netdev_info", "netdev_dbg",
    "unlikely", "likely",
    "IS_ERR", "PTR_ERR", "ERR_PTR", "ERR_CAST",
    "EXPORT_SYMBOL", "EXPORT_SYMBOL_GPL",
    "MODULE_LICENSE", "MODULE_AUTHOR", "MODULE_DESCRIPTION",
    "DEFINE_MUTEX", "DEFINE_SPINLOCK", "DECLARE_WORK",
    "LIST_HEAD", "INIT_LIST_HEAD", "INIT_WORK", "INIT_DELAYED_WORK",
}

# Known kernel/library leaf functions — don't recurse into these
KERNEL_LEAF_FUNCS = {
    "kfree", "kmalloc", "kzalloc", "kcalloc", "krealloc", "kvfree",
    "vmalloc", "vzalloc", "vfree",
    "memcpy", "memset", "memmove", "memcmp",
    "strlen", "strcmp", "strncmp", "strcpy", "strncpy", "strlcpy", "strlcat",
    "snprintf", "sprintf", "sscanf", "scnprintf",
    "copy_to_user", "copy_from_user",
    "spin_lock", "spin_unlock", "spin_lock_irqsave", "spin_unlock_irqrestore",
    "spin_lock_bh", "spin_unlock_bh", "spin_lock_init",
    "mutex_lock", "mutex_unlock", "mutex_init", "mutex_trylock",
    "rtnl_lock", "rtnl_unlock", "wiphy_lock", "wiphy_unlock",
    "rcu_read_lock", "rcu_read_unlock",
    "napi_schedule", "napi_schedule_irqoff", "napi_complete", "napi_complete_done",
    "napi_enable", "napi_disable",
    "schedule_work", "schedule_delayed_work", "queue_work", "queue_delayed_work",
    "cancel_work_sync", "cancel_delayed_work", "cancel_delayed_work_sync",
    "flush_work", "flush_workqueue",
    "tasklet_schedule", "tasklet_init", "tasklet_kill",
    "netif_receive_skb", "netif_rx", "napi_gro_receive",
    "netif_wake_queue", "netif_stop_queue", "netif_start_queue",
    "netif_carrier_on", "netif_carrier_off",
    "netif_dormant_on", "netif_dormant_off",
    "skb_queue_tail", "skb_dequeue", "skb_queue_head", "skb_queue_len",
    "skb_queue_purge", "skb_queue_head_init",
    "alloc_skb", "dev_alloc_skb", "kfree_skb", "consume_skb",
    "skb_put", "skb_push", "skb_pull", "skb_reserve", "skb_copy",
    "skb_clone", "skb_trim", "pskb_trim",
    "eth_hdr", "be16_to_cpu", "cpu_to_be16", "be32_to_cpu", "cpu_to_be32",
    "htons", "ntohs", "htonl", "ntohl",
    "netdev_priv", "wiphy_priv", "ieee80211_vif_to_wdev",
    "cfg80211_scan_done", "cfg80211_connect_result", "cfg80211_connect_done",
    "cfg80211_disconnected", "cfg80211_roamed",
    "cfg80211_inform_bss_data", "cfg80211_put_bss",
    "ieee80211_queue_work", "ieee80211_wake_queues",
    "ieee80211_stop_queues", "ieee80211_queue_delayed_work",
    "request_firmware", "release_firmware",
    "dev_kfree_skb", "dev_kfree_skb_any", "dev_consume_skb_any",
    "jiffies_to_msecs", "msecs_to_jiffies",
    "mod_timer", "del_timer", "del_timer_sync",
    "wake_up", "wake_up_interruptible", "wait_event", "wait_event_timeout",
    "complete", "wait_for_completion", "wait_for_completion_timeout",
    "atomic_set", "atomic_read", "atomic_inc", "atomic_dec",
    "atomic_inc_return", "atomic_dec_return",
    "test_bit", "set_bit", "clear_bit",
    "ether_addr_copy", "is_zero_ether_addr", "is_broadcast_ether_addr",
    "is_multicast_ether_addr", "ether_addr_equal",
}

# Deferred execution scheduling APIs
DEFERRED_MECHANISMS = {
    "napi_schedule":            {"mechanism": "NAPI",    "exec_context": "softirq"},
    "napi_schedule_irqoff":     {"mechanism": "NAPI",    "exec_context": "softirq"},
    "schedule_work":            {"mechanism": "WQ",      "exec_context": "process"},
    "schedule_delayed_work":    {"mechanism": "WQ",      "exec_context": "process"},
    "queue_work":               {"mechanism": "WQ",      "exec_context": "process"},
    "queue_delayed_work":       {"mechanism": "WQ",      "exec_context": "process"},
    "tasklet_schedule":         {"mechanism": "TASKLET", "exec_context": "softirq"},
    "ieee80211_queue_work":     {"mechanism": "WQ",      "exec_context": "process"},
    "ieee80211_queue_delayed_work": {"mechanism": "WQ",  "exec_context": "process"},
}


def is_noise_call(name):
    """Return True if `name` is a known noise/macro call to skip."""
    for prefix in NOISE_PATTERNS:
        if name.startswith(prefix):
            return True
    return False


def is_all_upper_macro(name):
    """Return True if name looks like a macro (ALL_CAPS), excluding known exceptions."""
    if not name.isupper():
        return False
    # Allow known uppercase real functions
    EXCEPTIONS = {"SDEV_FROM_WIPHY"}
    return name not in EXCEPTIONS


class FunctionInfo:
    """Parsed info about a function definition."""
    __slots__ = ("name", "file", "line", "calls", "lock_ops")

    def __init__(self, name, file, line):
        self.name = name
        self.file = file
        self.line = line
        self.calls = []       # list of callee names (str)
        self.lock_ops = []    # list of {"op": "lock"|"unlock", "api": "mutex_lock", "line": N}


# Lock detection patterns
LOCK_APIS = {
    "spin_lock": "lock", "spin_unlock": "unlock",
    "spin_lock_irqsave": "lock", "spin_unlock_irqrestore": "unlock",
    "spin_lock_bh": "lock", "spin_unlock_bh": "unlock",
    "mutex_lock": "lock", "mutex_unlock": "unlock",
    "mutex_trylock": "lock",
    "rtnl_lock": "lock", "rtnl_unlock": "unlock",
    "wiphy_lock": "lock", "wiphy_unlock": "unlock",
    "rcu_read_lock": "lock", "rcu_read_unlock": "unlock",
}


def preprocess_file(filepath, include_paths=None, defines=None):
    """Preprocess a C file with clang -E.  Returns preprocessed source or None on failure."""
    cmd = ["clang", "-E",             # preprocess only (keep line markers for mapping)
           "-w",                       # suppress warnings
           "-nostdinc",                # don't use system includes (use only explicit -I)
           "-D__KERNEL__",             # kernel code marker
           ]
    for inc in (include_paths or []):
        cmd.extend(["-I", inc])
    for defn in (defines or []):
        cmd.extend(["-D", defn])
    cmd.append(filepath)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        # Accept partial output even if clang reports errors (missing headers).
        # Clang still expands macros and resolves #ifdef in reachable code.
        if result.stdout and len(result.stdout) > 100:
            return result.stdout
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


_LINE_MARKER_RE = re.compile(r'^#\s+(\d+)\s+"([^"]+)"')


def build_line_map(source):
    """Build a mapping from physical line numbers to (original_file, original_line).

    Parses clang -E line markers of the form:  # N "file" [flags]
    Returns a sorted list of (physical_line, orig_file, orig_line_start).
    """
    markers = []
    for phys_line, text in enumerate(source.split('\n'), 1):
        m = _LINE_MARKER_RE.match(text)
        if m:
            orig_line = int(m.group(1))
            orig_file = os.path.basename(m.group(2))
            markers.append((phys_line, orig_file, orig_line))
    return markers


def resolve_line(markers, phys_line):
    """Given a physical line in preprocessed output, return (orig_file, orig_line).

    Uses binary search on markers built by build_line_map().
    """
    if not markers:
        return None, phys_line

    # Binary search: find last marker with physical_line <= phys_line
    lo, hi = 0, len(markers) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if markers[mid][0] <= phys_line:
            lo = mid
        else:
            hi = mid - 1

    marker_phys, orig_file, orig_line_start = markers[lo]
    if marker_phys > phys_line:
        return None, phys_line

    orig_line = orig_line_start + (phys_line - marker_phys - 1)
    return orig_file, orig_line


def parse_file(filepath, filename, include_paths=None, defines=None):
    """Parse a single C file.  Returns (functions, ops_tables).

    If clang is available, preprocesses with clang -E first to resolve
    #ifdef blocks and macros.  Line markers from clang -E are used to
    map back to original source file names and line numbers.
    Falls back to raw source if clang fails.

    functions:  dict  name -> FunctionInfo
    ops_tables: list  [{"struct_type": ..., "var_name": ..., "assignments": {field: func}}]
    """
    # Try clang preprocessing first
    source = preprocess_file(filepath, include_paths, defines)
    line_markers = None
    if source is not None:
        line_markers = build_line_map(source)
    else:
        # Fallback: read raw source
        try:
            with open(filepath, "r", errors="replace") as f:
                source = f.read()
        except OSError:
            return {}, []

    tokens = list(tokenize_c(source, filename))
    functions = {}
    ops_tables = []

    i = 0
    n = len(tokens)

    def peek(offset=0):
        idx = i + offset
        return tokens[idx] if idx < n else Token(TokType.EOF, "", 0, 0)

    def find_func_body_start(start):
        """From a position after ')', find the opening '{' skipping attributes, etc.
        Returns index of '{' or -1."""
        j = start
        while j < n:
            t = tokens[j]
            if t.type == TokType.PUNCT and t.value == '{':
                return j
            if t.type == TokType.PUNCT and t.value == ';':
                return -1  # declaration, not definition
            if t.type == TokType.PUNCT and t.value == ',':
                return -1
            j += 1
        return -1

    def extract_brace_block(start):
        """Given index of '{', return index after matching '}' and all tokens inside."""
        depth = 0
        j = start
        body_tokens = []
        while j < n:
            t = tokens[j]
            if t.type == TokType.PUNCT and t.value == '{':
                depth += 1
            elif t.type == TokType.PUNCT and t.value == '}':
                depth -= 1
                if depth == 0:
                    return j + 1, body_tokens
            if depth >= 1:
                body_tokens.append(t)
            j += 1
        return j, body_tokens

    def extract_calls_from_body(body_tokens):
        """Extract function calls and lock ops from a token list."""
        calls = []
        lock_ops = []
        bt_len = len(body_tokens)
        for k in range(bt_len):
            t = body_tokens[k]
            if t.type != TokType.IDENT:
                continue
            # Look for IDENT followed by '('
            if k + 1 < bt_len and body_tokens[k + 1].type == TokType.PUNCT and body_tokens[k + 1].value == '(':
                name = t.value
                if name in C_KEYWORDS:
                    continue
                if is_noise_call(name):
                    continue
                if is_all_upper_macro(name):
                    continue
                calls.append(name)
                # Check for lock operations
                if name in LOCK_APIS:
                    lock_ops.append({"op": LOCK_APIS[name], "api": name, "line": t.line})
        return calls, lock_ops

    def try_parse_ops_table(start):
        """Try to parse a struct ops table initializer starting at token index `start`.
        Pattern: [static] [const] struct TYPE VAR = { .field = func, ... };
        Returns (end_index, table_info) or (start, None)."""
        j = start
        # Skip 'static', 'const'
        while j < n and tokens[j].type == TokType.IDENT and tokens[j].value in ('static', 'const'):
            j += 1
        if j >= n or tokens[j].type != TokType.IDENT or tokens[j].value != 'struct':
            return start, None
        j += 1
        if j >= n or tokens[j].type != TokType.IDENT:
            return start, None
        struct_type = tokens[j].value
        j += 1
        if j >= n or tokens[j].type != TokType.IDENT:
            return start, None
        var_name = tokens[j].value
        j += 1
        if j >= n or tokens[j].type != TokType.PUNCT or tokens[j].value != '=':
            return start, None
        j += 1
        if j >= n or tokens[j].type != TokType.PUNCT or tokens[j].value != '{':
            return start, None

        # Parse the initializer block
        end, body = extract_brace_block(j)
        assignments = {}
        k = 0
        blen = len(body)
        while k < blen:
            # Look for .field = identifier
            if (body[k].type == TokType.PUNCT and body[k].value == '.'
                    and k + 1 < blen and body[k + 1].type == TokType.IDENT
                    and k + 2 < blen and body[k + 2].type == TokType.PUNCT and body[k + 2].value == '='
                    and k + 3 < blen and body[k + 3].type == TokType.IDENT):
                field = body[k + 1].value
                func = body[k + 3].value
                if func not in C_KEYWORDS and not func[0].isdigit():
                    assignments[field] = func
                k += 4
            else:
                k += 1

        if assignments:
            return end, {
                "struct_type": struct_type,
                "var_name": var_name,
                "file": filename,
                "assignments": assignments,
            }
        return end, None

    # --- Main parse loop ---
    while i < n:
        t = tokens[i]

        # Skip preprocessor
        if t.type == TokType.PREPROC:
            i += 1
            continue

        # Try to detect ops table initializers
        # Heuristic: 'static' or 'const' followed eventually by 'struct'
        if (t.type == TokType.IDENT and t.value in ('static', 'const')
                and any(tokens[j].type == TokType.IDENT and tokens[j].value == 'struct'
                        for j in range(i, min(i + 4, n)))):
            end, table = try_parse_ops_table(i)
            if table:
                ops_tables.append(table)
                i = end
                continue

        # Try to detect function definitions
        # Heuristic: IDENT '(' at top level (brace depth 0)
        if t.type == TokType.IDENT and t.value not in C_KEYWORDS:
            # Check if next token is '('
            if i + 1 < n and tokens[i + 1].type == TokType.PUNCT and tokens[i + 1].value == '(':
                func_name = t.value
                func_line = t.line
                # Skip past the parameter list: find matching ')'
                j = i + 2
                paren_depth = 1
                while j < n and paren_depth > 0:
                    if tokens[j].type == TokType.PUNCT:
                        if tokens[j].value == '(':
                            paren_depth += 1
                        elif tokens[j].value == ')':
                            paren_depth -= 1
                    j += 1
                # j is now past ')'
                # Look for '{' (function body) — skip __attribute__, etc.
                body_start = find_func_body_start(j)
                if body_start >= 0:
                    end, body = extract_brace_block(body_start)
                    calls, lock_ops = extract_calls_from_body(body)

                    fi = FunctionInfo(func_name, filename, func_line)
                    fi.calls = calls
                    fi.lock_ops = lock_ops
                    functions[func_name] = fi

                    i = end
                    continue

        i += 1

    # Post-process: map preprocessed line numbers back to original source
    if line_markers:
        for fi in functions.values():
            orig_file, orig_line = resolve_line(line_markers, fi.line)
            if orig_file:
                fi.file = orig_file
                fi.line = orig_line
            for lock_op in fi.lock_ops:
                _, orig_lock_line = resolve_line(line_markers, lock_op["line"])
                lock_op["line"] = orig_lock_line
        for table in ops_tables:
            # Use the var_name token's line (approximated by first assignment)
            # The file is already set from the struct type context
            pass  # ops table file is set below

    # For ops tables, resolve file from the first assignment's function if available
    if line_markers:
        for table in ops_tables:
            # Find the physical line of the struct by checking token positions
            # Use the filename from the first assigned function as a proxy
            first_func = next(iter(table["assignments"].values()), None)
            if first_func and first_func in functions:
                table["file"] = functions[first_func].file

    return functions, ops_tables


# =========================================================================
# Part 3: Source Tree Scanner
# =========================================================================

def find_source_files(source_dir):
    """Find all .c and .h files, excluding test/kunit directories."""
    files = []
    for root, dirs, filenames in os.walk(source_dir):
        # Skip test directories
        dirs[:] = [d for d in dirs if d not in ('kunit', 'test', 'tests')]
        for f in filenames:
            if f.endswith(('.c', '.h')):
                files.append(os.path.join(root, f))
    return sorted(files)


def scan_source_tree(source_dir, include_paths=None, defines=None):
    """Scan entire source tree.  Returns (all_functions, all_ops_tables, file_count)."""
    files = find_source_files(source_dir)
    all_functions = {}    # func_name -> FunctionInfo
    all_ops_tables = []

    for fpath in files:
        fname = os.path.basename(fpath)
        funcs, ops = parse_file(fpath, fname, include_paths, defines)
        # Merge — prefer .c definitions over .h
        for name, fi in funcs.items():
            if name in all_functions:
                existing = all_functions[name]
                if existing.file.endswith('.h') and fi.file.endswith('.c'):
                    all_functions[name] = fi
                # else keep existing .c definition
            else:
                all_functions[name] = fi
        all_ops_tables.extend(ops)

    return all_functions, all_ops_tables, len(files)


# =========================================================================
# Part 4: Entry Point Detection
# =========================================================================

# Known ops struct types and their field → category mappings
OPS_CATEGORIES = {
    "cfg80211_ops": "cfg80211",
    "wiphy_vendor_command": "vendor",
    "net_device_ops": "netdev",
    "ieee80211_ops": "mac80211",
    "ethtool_ops": "ethtool",
}

# Fields that represent meaningful entry points
ENTRY_FIELDS = {
    # cfg80211
    "connect", "disconnect", "scan", "add_key", "del_key", "set_channel",
    "get_station", "dump_station", "set_pmksa", "del_pmksa", "flush_pmksa",
    "remain_on_channel", "cancel_remain_on_channel", "mgmt_tx",
    "set_wiphy_params", "resume", "suspend", "set_power_mgmt",
    "set_default_key", "add_virtual_intf", "del_virtual_intf",
    "change_virtual_intf", "set_monitor_channel", "start_ap", "stop_ap",
    "change_bss", "set_txq_params", "sched_scan_start", "sched_scan_stop",
    "update_ft_ies", "set_rekey_data", "tdls_oper", "tdls_mgmt",
    # netdev
    "ndo_open", "ndo_stop", "ndo_start_xmit", "ndo_get_stats64",
    "ndo_set_mac_address", "ndo_set_rx_mode", "ndo_tx_timeout",
    "ndo_select_queue",
    # mac80211
    "tx", "start", "stop", "add_interface", "remove_interface", "config",
    "configure_filter", "sta_add", "sta_remove", "set_key", "ampdu_action",
    "hw_scan", "flush", "sw_scan_start", "sw_scan_complete",
}


def detect_entry_points(all_functions, ops_tables):
    """Detect entry points from ops tables and NAPI/ISR registrations."""
    entries = {}

    # 1. From ops tables
    for table in ops_tables:
        struct_type = table["struct_type"]
        category = OPS_CATEGORIES.get(struct_type, struct_type)
        for field, func in table["assignments"].items():
            if field in ENTRY_FIELDS and func in all_functions:
                entries[func] = {
                    "function": func,
                    "op": field,
                    "category": category,
                    "file": all_functions[func].file,
                    "via_struct": struct_type,
                }

    # 2. From NAPI/ISR registrations — scan function calls for
    #    netif_napi_add(dev, &napi, POLL_FUNC, ...) and request_irq(irq, ISR_FUNC, ...)
    napi_re = re.compile(r'netif_napi_add')
    irq_re = re.compile(r'request_irq')
    for fname, fi in all_functions.items():
        for callee in fi.calls:
            # Look for NAPI poll functions passed as arguments
            if callee == "netif_napi_add":
                # The poll function is typically the callee after netif_napi_add
                # We need to look at the actual function body — heuristic: find
                # functions in the same file ending with _poll or _napi_poll
                pass  # Handled below via name pattern

    # 3. Name-pattern heuristics for NAPI poll and ISR functions
    for fname, fi in all_functions.items():
        if fname.endswith(('_napi_poll', '_poll')) and fname not in entries:
            entries[fname] = {
                "function": fname,
                "op": "napi_poll",
                "category": "napi",
                "file": fi.file,
                "via_struct": None,
            }
        elif fname.endswith(('_isr', '_irq_handler', '_interrupt')) and fname not in entries:
            entries[fname] = {
                "function": fname,
                "op": "isr",
                "category": "interrupt",
                "file": fi.file,
                "via_struct": None,
            }

    return entries


# =========================================================================
# Part 5: Call Chain Extraction
# =========================================================================

def extract_call_chain(all_functions, entry_func, max_depth, exclude_prefixes):
    """Extract a call chain rooted at entry_func using BFS.

    Returns:
        nodes: list of node dicts
        edges: list of edge dicts
        deferred_triggers: list of deferred trigger dicts
    """
    nodes = {}
    edges = []
    deferred_triggers = []
    queue = [(entry_func, 0)]
    visited = set()

    while queue:
        func_name, depth = queue.pop(0)

        if func_name in visited:
            continue
        visited.add(func_name)

        if max_depth > 0 and depth > max_depth:
            continue

        if any(func_name.startswith(p) for p in exclude_prefixes):
            continue

        fi = all_functions.get(func_name)
        is_leaf = func_name in KERNEL_LEAF_FUNCS

        callees = []
        lock_ops = []

        if fi and not is_leaf:
            # Deduplicate calls while preserving order
            seen_calls = set()
            for callee in fi.calls:
                if callee in seen_calls:
                    continue
                seen_calls.add(callee)
                callees.append(callee)

                edge_type = "direct"
                if callee in DEFERRED_MECHANISMS:
                    edge_type = "deferred_schedule"
                    mech = DEFERRED_MECHANISMS[callee]
                    deferred_triggers.append({
                        "trigger_func": func_name,
                        "schedule_api": callee,
                        "mechanism": mech["mechanism"],
                        "exec_context": mech["exec_context"],
                    })

                edges.append({
                    "caller": func_name,
                    "callee": callee,
                    "type": edge_type,
                })

                # Recurse into callees that we have definitions for
                if (callee not in visited
                        and callee not in KERNEL_LEAF_FUNCS
                        and callee in all_functions):
                    queue.append((callee, depth + 1))

            lock_ops = fi.lock_ops

        nodes[func_name] = {
            "name": func_name,
            "file": fi.file if fi else None,
            "line": fi.line if fi else None,
            "depth": depth,
            "callees": callees,
            "is_kernel_leaf": is_leaf,
            "lock_ops": lock_ops,
        }

    # Filter: only include nodes that have a source file definition
    filtered_nodes = [n for n in nodes.values() if n["file"] is not None]
    return filtered_nodes, edges, deferred_triggers


# =========================================================================
# Part 6: Main
# =========================================================================

# =========================================================================
# Part 7: Variant Detection
# =========================================================================

def _parse_makefile_tier1(makefile_path):
    """Tier 1: Find configs that switch .o file targets in Makefile.

    Looks for patterns like:
        ifeq ($(CONFIG_X),y)
        obj += file_a.o
        else
        obj += file_b.o
        endif

    Returns list of dicts with config, files_when_set, files_when_unset.
    """
    try:
        with open(makefile_path, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []

    results = []
    ifeq_re = re.compile(
        r'^\s*ifeq\s+\([$]\((\w+)\)\s*,\s*y\)')
    obj_re = re.compile(
        r'[\w-]+[$]\([^)]+\)\s*\+=\s*([\w.]+\.o)')

    # Scan every ifeq independently (don't skip lines consumed by outer blocks)
    for i, raw_line in enumerate(lines):
        m = ifeq_re.match(raw_line)
        if not m:
            continue

        config = m.group(1)
        # Collect .o files in the if-branch and else-branch at depth 1 relative to this ifeq
        if_objs = []
        else_objs = []
        in_else = False
        depth = 1
        j = i + 1
        while j < len(lines) and depth > 0:
            line = lines[j].strip()
            if line.startswith(('ifeq', 'ifneq', 'ifdef', 'ifndef')):
                depth += 1
            elif line == 'endif':
                depth -= 1
            elif line == 'else' and depth == 1:
                in_else = True
                j += 1
                continue

            if depth == 1:
                om = obj_re.search(lines[j])
                if om:
                    obj_file = om.group(1)
                    if in_else:
                        else_objs.append(obj_file)
                    else:
                        if_objs.append(obj_file)
            j += 1

        # Only report if both branches have .o files (actual file switch)
        if if_objs and else_objs:
            results.append({
                "config": config,
                "files_when_set": if_objs,
                "files_when_unset": else_objs,
            })

    return results


def _parse_makefile_tier2(makefile_path):
    """Tier 2: Find configs passed as -D flags via ccflags in Makefile.

    Looks for patterns like:
        ccflags-$(CONFIG_X) += -DCONFIG_X
        ccflags-y += -DCONFIG_X   (inside ifeq blocks)

    Returns list of config names.
    """
    try:
        with open(makefile_path, "r", errors="replace") as f:
            content = f.read()
    except OSError:
        return []

    configs = set()

    # Pattern 1: ccflags-$(CONFIG_X) += -DCONFIG_X
    for m in re.finditer(r'ccflags-[$]\((\w+)\)\s*\+=\s*-D(\w+)', content):
        configs.add(m.group(2))

    # Pattern 2: ccflags-y += -DCONFIG_X (standalone or inside ifeq)
    for m in re.finditer(r'ccflags-y\s*\+=\s*-D(\w+)', content):
        configs.add(m.group(1))

    return sorted(configs)


def _parse_kconfig_selects(kconfig_path):
    """Parse Kconfig for 'select' dependencies.

    Returns dict: config_name -> list of selected configs.
    """
    try:
        with open(kconfig_path, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return {}

    selects = {}
    current_config = None

    for line in lines:
        stripped = line.strip()
        # Match 'config CONFIG_NAME' or 'config NAME'
        cm = re.match(r'^config\s+(\w+)', stripped)
        if cm:
            current_config = cm.group(1)
            # Normalize: add CONFIG_ prefix if not present
            if not current_config.startswith('CONFIG_'):
                current_config = 'CONFIG_' + current_config
            continue

        if current_config and stripped.startswith('select'):
            sm = re.match(r'select\s+(\w+)', stripped)
            if sm:
                selected = sm.group(1)
                if not selected.startswith('CONFIG_'):
                    selected = 'CONFIG_' + selected
                selects.setdefault(current_config, []).append(selected)

    return selects


def _scan_source_ifdef(source_dir):
    """Tier 3: Scan source files for #ifdef CONFIG_* blocks and count guarded lines.

    Returns dict: config_name -> {"files": set, "guarded_lines": int}.
    """
    files = find_source_files(source_dir)
    config_re = re.compile(
        r'#\s*(?:ifdef|ifndef)\s+(CONFIG_\w+)'
        r'|#\s*if\s+.*?\bdefined\s*\(\s*(CONFIG_\w+)\s*\)')

    stats = {}  # config -> {"files": set, "guarded_lines": int}

    for fpath in files:
        try:
            with open(fpath, "r", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue

        fname = os.path.relpath(fpath, source_dir)
        # Track nested #if depth and which configs are active at each depth
        depth_stack = []  # stack of config names (or None for non-CONFIG #if)

        i = 0
        while i < len(lines):
            stripped = lines[i].strip()

            # Match #ifdef CONFIG_X or #if defined(CONFIG_X)
            cm = config_re.match(stripped)
            if cm:
                config = cm.group(1) or cm.group(2)
                depth_stack.append(config)
                i += 1
                continue

            # Match other #if / #ifdef / #ifndef (non-CONFIG)
            if re.match(r'#\s*(?:if|ifdef|ifndef)\b', stripped):
                depth_stack.append(None)
                i += 1
                continue

            # Match #endif
            if re.match(r'#\s*endif\b', stripped):
                if depth_stack:
                    depth_stack.pop()
                i += 1
                continue

            # Match #else / #elif — attribute lines to the config at current depth
            if re.match(r'#\s*(?:else|elif)\b', stripped):
                i += 1
                continue

            # Count this line for all CONFIG_* in the depth stack
            for config in depth_stack:
                if config is not None:
                    if config not in stats:
                        stats[config] = {"files": set(), "guarded_lines": 0}
                    stats[config]["files"].add(fname)
                    stats[config]["guarded_lines"] += 1

            i += 1

    return stats


def detect_variants(source_dir, min_lines=50):
    """Detect kernel config variants using three-tier analysis.

    Tier 1: Makefile .o file switches (highest impact)
    Tier 2: Makefile ccflags -D defines (medium impact)
    Tier 3: Source #ifdef guarded lines above threshold (variable impact)

    Returns a structured dict with all tiers and grouping info.
    """
    makefile_path = os.path.join(source_dir, "Makefile")
    kconfig_path = os.path.join(source_dir, "Kconfig")

    # --- Tier 1 ---
    tier1 = _parse_makefile_tier1(makefile_path)
    tier1_configs = set()
    for entry in tier1:
        tier1_configs.add(entry["config"])
        # Normalize
        if not entry["config"].startswith("CONFIG_"):
            entry["config"] = "CONFIG_" + entry["config"]
            tier1_configs.add(entry["config"])

    # --- Tier 2 ---
    tier2_all = _parse_makefile_tier2(makefile_path)
    # Exclude configs already in Tier 1
    tier2 = [c for c in tier2_all if c not in tier1_configs
             and not any(c == e["config"] or c == e["config"].replace("CONFIG_", "", 1)
                         for e in tier1)]

    # --- Kconfig select dependencies ---
    kconfig_selects = _parse_kconfig_selects(kconfig_path)

    # --- Tier 3 ---
    ifdef_stats = _scan_source_ifdef(source_dir)
    tier3 = []
    tier12_configs = tier1_configs | set(tier2)
    for config, info in sorted(ifdef_stats.items(),
                                key=lambda x: -x[1]["guarded_lines"]):
        if config in tier12_configs:
            continue
        # Also skip if it's a bare name match (without CONFIG_ prefix)
        bare = config.replace("CONFIG_", "", 1)
        if bare in tier12_configs or ("CONFIG_" + bare) in tier12_configs:
            continue
        if info["guarded_lines"] >= min_lines:
            tier3.append({
                "config": config,
                "guarded_lines": info["guarded_lines"],
                "file_count": len(info["files"]),
                "files": sorted(info["files"]),
            })

    # --- Grouping: resolve select dependencies ---
    def get_implied(config):
        """Get all configs implied by 'select' for a given config."""
        implied = []
        # Try both with and without CONFIG_ prefix
        for key in [config, config.replace("CONFIG_", "", 1),
                    "CONFIG_" + config.replace("CONFIG_", "", 1)]:
            if key in kconfig_selects:
                implied.extend(kconfig_selects[key])
        return implied

    # Build variant groups
    variants = []
    grouped_configs = set()

    # Tier 1 variants
    for entry in tier1:
        config = entry["config"]
        implied = get_implied(config)
        defines = list(dict.fromkeys([config] + implied))  # deduplicate, preserve order
        variants.append({
            "name": config.replace("CONFIG_", "").lower(),
            "tier": 1,
            "primary_config": config,
            "defines": defines,
            "files_when_set": entry["files_when_set"],
            "files_when_unset": entry["files_when_unset"],
        })
        grouped_configs.add(config)
        grouped_configs.update(implied)

    # Tier 2 standalone (not already grouped into a Tier 1 variant)
    for config in tier2:
        if config in grouped_configs:
            continue
        implied = get_implied(config)
        variants.append({
            "name": config.replace("CONFIG_", "").lower(),
            "tier": 2,
            "primary_config": config,
            "defines": list(dict.fromkeys([config] + implied)),
        })
        grouped_configs.add(config)
        grouped_configs.update(implied)

    # Tier 3 standalone
    for entry in tier3:
        config = entry["config"]
        if config in grouped_configs:
            continue
        implied = get_implied(config)
        variants.append({
            "name": config.replace("CONFIG_", "").lower(),
            "tier": 3,
            "primary_config": config,
            "defines": list(dict.fromkeys([config] + implied)),
            "guarded_lines": entry["guarded_lines"],
            "file_count": entry["file_count"],
        })
        grouped_configs.add(config)
        grouped_configs.update(implied)

    return {
        "source_dir": source_dir,
        "min_lines_threshold": min_lines,
        "tier1_count": len(tier1),
        "tier2_count": len(tier2),
        "tier3_count": len(tier3),
        "variant_count": len(variants),
        "variants": variants,
        "kconfig_selects": {k: v for k, v in kconfig_selects.items()
                           if any(k == ve.get("primary_config", "")
                                  or k.replace("CONFIG_", "", 1) == ve.get("primary_config", "").replace("CONFIG_", "", 1)
                                  for ve in variants)},
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract call traces from C source code using built-in tokenizer."
    )
    parser.add_argument("source_dir", help="Path to source directory")
    parser.add_argument("--output", "-o", help="Output JSON file (default: stdout)")
    parser.add_argument("--entry", "-e", action="append", default=[],
                        help="Entry point function name (repeatable)")
    parser.add_argument("--auto-detect", "-a", action="store_true",
                        help="Auto-detect entry points from ops tables")
    parser.add_argument("--max-depth", "-d", type=int, default=0,
                        help="Maximum call chain depth (default: 0 = unlimited)")
    parser.add_argument("--exclude-prefix", "-x", action="append", default=[],
                        help="Exclude functions matching prefix (repeatable)")
    parser.add_argument("--include", "-I", action="append", default=[],
                        dest="include_paths",
                        help="Additional include path for clang -E (repeatable)")
    parser.add_argument("--define", "-D", action="append", default=[],
                        dest="defines",
                        help="Additional macro definition for clang -E (repeatable)")
    parser.add_argument("--variant", "-V",
                        help="Variant name tag for this extraction run "
                             "(e.g., 'variant_a', 'variant_b'). Stored in "
                             "output JSON as 'variant' field for "
                             "per-variant comparison.")
    parser.add_argument("--detect-variants", action="store_true",
                        help="Detect kernel config variants and print "
                             "results as JSON. Does not extract call chains. "
                             "Uses three-tier analysis: Makefile .o switches, "
                             "Makefile ccflags, and source #ifdef scanning.")
    parser.add_argument("--min-lines", type=int, default=50,
                        help="Minimum guarded line count for Tier 3 variant "
                             "detection (default: 50)")
    args = parser.parse_args()

    source_dir = os.path.abspath(args.source_dir)
    if not os.path.isdir(source_dir):
        print(f"Error: {source_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # --- Variant detection mode ---
    if args.detect_variants:
        print(f"Detecting variants in: {source_dir}...", file=sys.stderr)
        result = detect_variants(source_dir, min_lines=args.min_lines)
        print(f"  Tier 1 (source file switches): {result['tier1_count']}",
              file=sys.stderr)
        print(f"  Tier 2 (build flag configs):    {result['tier2_count']}",
              file=sys.stderr)
        print(f"  Tier 3 (source #ifdef >= {args.min_lines} lines): "
              f"{result['tier3_count']}", file=sys.stderr)
        print(f"  Total variant candidates:       {result['variant_count']}",
              file=sys.stderr)

        output_json = json.dumps(result, indent=2)
        if args.output:
            with open(args.output, "w") as f:
                f.write(output_json + "\n")
            print(f"Written to {args.output}", file=sys.stderr)
        else:
            print(output_json)
        sys.exit(0)

    # Check clang availability
    if shutil.which("clang"):
        print("Using clang for preprocessing", file=sys.stderr)
    else:
        print("WARNING: clang not found — falling back to raw source parsing",
              file=sys.stderr)
        print("  Install clang for accurate #ifdef resolution", file=sys.stderr)

    # Build include paths: always include the source dir itself
    include_paths = [source_dir] + [os.path.abspath(p) for p in args.include_paths]

    # Scan source tree
    print(f"Scanning source tree: {source_dir}...", file=sys.stderr)
    all_functions, ops_tables, file_count = scan_source_tree(
        source_dir, include_paths, args.defines
    )
    print(f"  {file_count} files, {len(all_functions)} functions, "
          f"{len(ops_tables)} ops tables", file=sys.stderr)

    # Determine entry points
    entry_funcs = {}
    if args.auto_detect:
        entry_funcs = detect_entry_points(all_functions, ops_tables)
        print(f"  Auto-detected {len(entry_funcs)} entry points", file=sys.stderr)

    for func in args.entry:
        if func not in entry_funcs:
            entry_funcs[func] = {
                "function": func,
                "op": "user-specified",
                "category": "manual",
                "file": all_functions[func].file if func in all_functions else None,
                "via_struct": None,
            }

    if not entry_funcs:
        print("Error: no entry points. Use --entry <func> or --auto-detect",
              file=sys.stderr)
        sys.exit(1)

    # Build result
    result = {
        "source_dir": source_dir,
        "variant": args.variant,
        "defines": args.defines,
        "total_files": file_count,
        "total_functions": len(all_functions),
        "ops_tables": [
            {"struct_type": t["struct_type"], "var_name": t["var_name"],
             "file": t["file"], "field_count": len(t["assignments"]),
             "assignments": t["assignments"]}
            for t in ops_tables
        ],
        "entry_points": [],
        "call_traces": [],
    }

    for func_name, info in sorted(entry_funcs.items()):
        print(f"  Extracting: {func_name} ({info['op']})...", file=sys.stderr)
        nodes, edges, deferred = extract_call_chain(
            all_functions, func_name, args.max_depth, args.exclude_prefix
        )

        result["entry_points"].append(info)
        result["call_traces"].append({
            "entry": func_name,
            "op": info["op"],
            "category": info.get("category", "unknown"),
            "nodes": nodes,
            "edges": edges,
            "deferred_triggers": deferred,
            "node_count": len(nodes),
            "edge_count": len(edges),
        })

    # Output
    output_json = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json + "\n")
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
