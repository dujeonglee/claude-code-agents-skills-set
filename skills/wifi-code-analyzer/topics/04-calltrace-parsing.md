# Topic 04 — Calltrace Parsing (R1 Call Order + R2 Context Tracking)

You are a subagent performing calltrace analysis.
Your input is a raw call trace (any format) and optionally scan data.
Your output is an annotated function list with layer and context information.

Also read: `topics/05-deferred-context.md` for deferred execution linkage.
Also read: `topics/06-locks-and-functions.md` for lock and per-function analysis.
Also read: `topics/08-wifi-domain-knowledge.md` for WiFi domain reference.

---

## R1: Function Call Order Identification

### Step 1: Detect Trace Format

Identify which format the input uses:

| Format          | Indicators                                                   |
|-----------------|--------------------------------------------------------------|
| ftrace          | `funcA() { funcB() { ... } }` or `function_graph` header    |
| dmesg           | `[<timestamp>]` prefix, `Call Trace:` header, ` funcA+0x40` |
| perf            | Numeric addresses, `cycles:`, indented call chains           |
| manual/pseudo   | `→` arrows, `└──` tree characters, plain function names     |

### Step 2: Extract Function List

Parse every function name from the trace in **caller → callee** order.

- Strip offset notation: `funcA+0x40/0x80` → `funcA`
- Preserve indentation depth to reconstruct the call tree
- For dmesg stack traces, note that the order is callee-first (deepest first);
  **reverse** the list to get caller → callee order

### Step 3: Annotate Layer Boundaries

Assign each function to one of the five layers (see Topic 08):

1. **Kernel Standard Interface** — cfg80211/mac80211/netdev framework functions
2. **Driver Entry Points** — driver's implementations of framework callbacks
3. **Driver Internal Logic** — internal function chains, state machines
4. **HW Abstraction Layer** — HAL/HIF functions
5. **Firmware Interface** — FW command/event functions

Mark every transition between layers explicitly:
```
[Layer: Kernel Std Interface] cfg80211_connect()
  └── [LAYER TRANSITION: Kernel → Driver Entry]
      [Layer: Driver Entry] drv_connect()
```

Distinguish:
- **Cross-layer call**: function in layer N calls function in layer N+1 or N-1
- **Lateral call**: function calls another within the same layer

---

## R2: Execution Context Tracking

### Step 4: Annotate Every Function with Context

Every function gets exactly one context annotation:

| Context         | Indicators                                              |
|-----------------|---------------------------------------------------------|
| process         | Default for cfg80211 ops, module init, ioctl handlers   |
| softirq         | Called from NET_RX_SOFTIRQ/NET_TX_SOFTIRQ, NAPI poll   |
| hardirq         | ISR handlers, `_isr` suffix, registered with request_irq |
| tasklet         | Registered via `tasklet_init`, runs in softirq          |
| workqueue       | Functions scheduled via `schedule_work`, `queue_work`   |
| NAPI poll       | The `napi_poll` callback and functions it calls         |

### Step 5: Identify Context Transition Points

A context transition occurs when:
- `napi_schedule()` is called → hardirq to softirq
- `schedule_work()` / `queue_work()` → current context to process (workqueue)
- `tasklet_schedule()` → current context to softirq (tasklet)
- An ISR fires → transitions into hardirq context

For each transition, record:
- **Trigger function**: the function that initiates the transition
- **Trigger context**: the context at the trigger site
- **Mechanism**: the scheduling API used
- **Execution entry**: the function that runs in the new context
- **Execution context**: the new context

---

## Output Structure

Produce a JSON structure for downstream processing:

```json
{
  "functions": [
    {
      "name": "cfg80211_connect",
      "layer": "Kernel Standard Interface",
      "context": "process",
      "depth": 0,
      "calls": ["drv_connect"],
      "layer_transition": null
    },
    {
      "name": "drv_connect",
      "layer": "Driver Entry Points",
      "context": "process",
      "depth": 1,
      "calls": ["driver_connect_request"],
      "layer_transition": "Kernel Std Interface → Driver Entry"
    }
  ],
  "context_transitions": [
    {
      "trigger_func": "driver_isr",
      "trigger_context": "hardirq",
      "mechanism": "napi_schedule",
      "exec_entry": "driver_poll",
      "exec_context": "softirq",
      "tag_id": "NAPI#RX"
    }
  ],
  "trace_format": "manual"
}
```

---

## DO

- DO detect the trace format before parsing — each format has different ordering rules.
- DO reverse dmesg stack traces to get caller→callee order.
- DO annotate every single function with both layer and context.
- DO mark every layer transition explicitly.
- DO identify all context transition points even when the deferred execution trace is not provided.

## DON'T

- DON'T assume all traces are in the same format — detect first.
- DON'T skip functions that appear to be kernel infrastructure (e.g., `net_rx_action`) — they are important context markers.
- DON'T assign a function to two layers — pick the primary one.
- DON'T guess the context when unsure — mark it as `"context": "unknown"` and note why.
- DON'T confuse NAPI poll context with generic softirq — NAPI poll is a specific sub-context.
