# Topic 06 — Lock Analysis (R3) + Per-Function Analysis (R4)

You are performing lock relationship analysis and per-function responsibility
summaries as part of calltrace analysis.

Also read: `topics/08-wifi-domain-knowledge.md` for WiFi lock semantics.

---

## R3: Lock Relationship Analysis

### Step 1: Identify Locks

Scan every function in the trace for lock operations. Lock types to detect:

| Lock API                        | Lock Type    | Context Restriction           |
|---------------------------------|-------------|-------------------------------|
| `spin_lock()` / `spin_unlock()` | spinlock    | Any context                   |
| `spin_lock_irqsave()` / `spin_unlock_irqrestore()` | spinlock-irq | Disables IRQs |
| `spin_lock_bh()` / `spin_unlock_bh()` | spinlock-bh | Disables softirq    |
| `mutex_lock()` / `mutex_unlock()` | mutex      | Process context only          |
| `rcu_read_lock()` / `rcu_read_unlock()` | RCU   | Any context, no sleeping     |
| `rtnl_lock()` / `rtnl_unlock()` | rtnl        | Process context only          |
| `wiphy_lock()` / `wiphy_unlock()` | wiphy      | Process context only          |

### Step 2: Track Lock Nesting

Record the lock acquisition order as a sequence:

```
cfg80211_connect:     rtnl_lock → wiphy_lock
  drv_connect:        (holds rtnl + wiphy) → driver_priv_lock
    send_fw_cmd:      (holds rtnl + wiphy + priv)
  drv_connect return: driver_priv_lock released
cfg80211_connect return: wiphy_lock released → rtnl_lock released
```

Build a nesting graph: if lock A is held when lock B is acquired, record
the edge A → B.

### Step 3: Validate Context-Appropriate Usage

Check each lock against its context restriction:

| Violation                                          | Severity |
|----------------------------------------------------|----------|
| `mutex_lock` called in softirq or hardirq context  | **BUG**  |
| `mutex_lock` called while holding a spinlock        | **BUG**  |
| `spin_lock` (non-BH) in code reachable from softirq | WARNING  |
| Lock ordering violation (see Topic 08)             | **DEADLOCK RISK** |
| Lock held across a context transition              | **DANGER** |

### Step 4: Record Lock Scope

For each lock, record:
- **Acquire function**: which function calls `*_lock()`
- **Release function**: which function calls `*_unlock()`
- **Scope span**: list of functions called while the lock is held
- **Cross-context**: does the scope cross a context boundary? (flag as dangerous)

---

## R4: Per-Function Responsibility Analysis

### One-Line Summary

For every function in the trace, write a one-line summary:

```
driver_connect_request: Translates cfg80211 connect params into FW command format
driver_send_fw_cmd:     Enqueues command to firmware command ring buffer
driver_isr:             Top-half interrupt handler; acknowledges IRQ and schedules NAPI
```

### Layer Separation Checks

Flag these anomalies:

| Anomaly               | Description                                            |
|-----------------------|--------------------------------------------------------|
| Layer skipping        | Driver internal logic calling cfg80211 internals directly |
| Reverse dependency    | Lower layer calling upper layer directly (not via callback) |
| Sleeping in atomic    | Function that sleeps called from hardirq/softirq context |
| Lock across schedule  | Lock held across a scheduling point (sleep, wait, schedule) |
| FW cmd from hardirq   | Firmware command issued from hardirq without deferral   |

---

## Output Structure

### Lock Analysis

```json
{
  "locks": [
    {
      "name": "rtnl_lock",
      "type": "rtnl",
      "acquire_func": "cfg80211_connect",
      "release_func": "cfg80211_connect",
      "scope": ["cfg80211_connect", "drv_connect", "driver_connect_request"],
      "context": "process",
      "cross_context": false
    }
  ],
  "nesting_order": [
    ["rtnl_lock", "wiphy_lock", "driver_priv_lock"]
  ],
  "violations": [
    {
      "type": "ordering_violation",
      "description": "wiphy_lock acquired before rtnl_lock in path X",
      "severity": "DEADLOCK RISK",
      "functions": ["func_a", "func_b"]
    }
  ]
}
```

### Function Summaries

```json
{
  "function_summaries": [
    {
      "name": "driver_connect_request",
      "summary": "Translates cfg80211 connect params into FW command format",
      "layer": "Driver Internal Logic",
      "anomalies": []
    }
  ]
}
```

---

## DO

- DO check the mandatory lock ordering from Topic 08: `rtnl > wiphy > local->mtx > driver_priv`.
- DO flag every context-inappropriate lock usage as a violation.
- DO record the full scope of each lock (all functions called while held).
- DO write a one-line summary for every function — no exceptions.
- DO flag layer-skipping and reverse-dependency patterns.

## DON'T

- DON'T ignore RCU read locks — they have implicit context restrictions (no sleeping).
- DON'T assume lock ordering is safe just because the code runs without a BUG — order violations are latent deadlocks.
- DON'T write multi-sentence function summaries — one line only.
- DON'T skip the cross-context check for lock scope — this catches the most dangerous bugs.
- DON'T flag normal callback patterns as "reverse dependency" — upper-layer callbacks registered by the lower layer are expected.
