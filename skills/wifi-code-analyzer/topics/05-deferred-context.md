# Topic 05 — Deferred / Asynchronous Context Linkage (R2-EXT)

**This is the KEY requirement for calltrace analysis.**

When a call trace that TRIGGERS deferred execution is physically separate
from the call trace that EXECUTES the deferred work, the two must be
explicitly linked in the analysis output.

---

## The Problem

Many WiFi driver operations split across two contexts:

```
TRIGGER SITE (hardirq)          EXECUTION SITE (softirq)
  driver_isr()                    net_rx_action()
    └── napi_schedule(...)          └── driver_poll()
                                          └── driver_rx_process()
```

These appear as separate traces but are **causally connected**.
Without explicit linkage, the analysis misses the most important
execution flow relationships.

---

## Three Linkage Rules

### Rule (a): TAG the Trigger Site as ORIGIN

At the function that schedules deferred work, record:
- **Tag ID**: `MECHANISM#PURPOSE` (e.g., `NAPI#RX`, `WQ#SCAN`, `TASKLET#TX_COMP`)
- **Scheduling function**: `napi_schedule`, `schedule_work`, `tasklet_schedule`, etc.
- **Data structure**: The struct carrying deferred context (`napi_struct`, `work_struct`, etc.)
- **Trigger context**: The execution context at trigger time

Annotation format:
```
driver_isr()  [hardirq]
  └── napi_schedule(&priv->napi)   ← ORIGIN: NAPI#RX
        [raises NET_RX_SOFTIRQ]
```

### Rule (b): TAG the Execution Site with BACK-REFERENCE

At the deferred entry point, record:
- **Tag ID**: Same as the ORIGIN tag
- **Entry function**: The function that runs in the new context
- **Execution context**: The new context at runtime
- **Back-reference**: The ORIGIN function and its context

Annotation format:
```
net_rx_action()  [softirq]   ← BACK-REF: NAPI#RX
  └── driver_poll()
        └── driver_rx_process()
```

### Rule (c): CONNECT in All Output Artifacts

Both sites must be connected in every output:

| Output    | How to Connect                                              |
|-----------|-------------------------------------------------------------|
| Diagram   | Dashed arrow from ORIGIN to EXECUTION, labeled with mechanism and context change |
| Table     | Cross-reference ORIGIN and EXECUTION rows by shared Tag ID  |
| Context   | List the deferred pair in the Context Transition Summary     |

---

## Full Example: NAPI RX Path

```
TRIGGER SITE (hardirq context):
  driver_isr()                              [hardirq]
    └── napi_schedule(&priv->napi)          [ORIGIN: NAPI#RX]
          [raises NET_RX_SOFTIRQ]

EXECUTION SITE (softirq context):
  net_rx_action()                           [softirq, BACK-REF: NAPI#RX]
    └── napi_poll()
          └── driver_poll()
                └── driver_rx_process()
                      └── netif_receive_skb()

Linkage annotation:
  "driver_poll() is the execution site of NAPI#RX,
   originally scheduled by driver_isr() → napi_schedule() in hardirq context"
```

---

## Mechanisms Requiring Linkage

All of these produce trigger/execution splits and need the same treatment:

| Mechanism                    | Trigger Function             | Execution Context | Tag Example      |
|------------------------------|-----------------------------|--------------------|------------------|
| NAPI                         | `napi_schedule()`           | softirq (NAPI)     | `NAPI#RX`        |
| Workqueue                    | `schedule_work()`           | process (kworker)  | `WQ#FW_EVENT`    |
| Delayed workqueue            | `schedule_delayed_work()`   | process (kworker)  | `WQ#WATCHDOG`    |
| Tasklet                      | `tasklet_schedule()`        | softirq (tasklet)  | `TASKLET#TX_COMP`|
| mac80211 workqueue           | `ieee80211_queue_work()`    | process (mac80211) | `WQ#MAC_WORK`    |
| cfg80211 scheduled scan      | `cfg80211_sched_scan_results()` | nl80211 event  | `NL#SCHED_SCAN`  |

---

## Tag ID Convention

Format: `MECHANISM#PURPOSE`

- MECHANISM: `NAPI`, `WQ`, `TASKLET`, `NL`, `TIMER`
- PURPOSE: Short description of what the deferred work does
- Examples: `NAPI#RX`, `WQ#SCAN`, `WQ#FW_EVENT`, `TASKLET#TX_COMP`

Tag IDs must be unique within a single analysis. If two NAPI paths exist
(e.g., RX and TX completion), use `NAPI#RX` and `NAPI#TX_COMP`.

---

## DO

- DO create a Tag ID for every deferred execution pair found in the trace.
- DO annotate both the ORIGIN and the BACK-REFERENCE with the same Tag ID.
- DO include deferred linkage in the diagram (dashed arrow), table (cross-reference), and context summary.
- DO look for all six mechanism types listed above, not just NAPI.
- DO record the data structure carrying the deferred context (e.g., `struct napi_struct`).
- DO link even when trigger and execution appear in separate trace fragments.

## DON'T

- DON'T omit the linkage — this is the KEY requirement. Unlinked deferred paths are the #1 failure mode.
- DON'T use solid arrows for deferred execution in diagrams — use dashed arrows.
- DON'T invent Tag IDs that don't follow the `MECHANISM#PURPOSE` convention.
- DON'T assume a trigger and execution are in the same context — they almost never are.
- DON'T skip the back-reference annotation on the execution site — both ends must be tagged.
