# Topic 08 — WiFi Driver Domain Knowledge

Shared reference for both Module Inventory and Calltrace Analysis skills.
Read this file when you need WiFi-specific context for any analysis task.

---

## File Name Pattern → Module Mapping

Use these patterns as **starting hints only**. Symbol evidence (shared structs,
function-call chains) overrides pattern hints when they conflict.

| Pattern Keywords               | Candidate Module           |
|--------------------------------|----------------------------|
| scan, bss, probe              | Scan & Discovery           |
| auth, assoc, sme, mlme        | MAC Management             |
| tx, rx, xmit, ampdu           | Data Path (Tx/Rx)          |
| phy, rf, cal, antenna         | PHY Layer                  |
| fw, hif, cmd, event           | Firmware Interface         |
| pm, ps, wow, sleep            | Power Management           |
| reg, regulatory, dfs          | Regulatory                 |
| debug, trace, stat            | Debug & Diagnostics        |
| util, common, helper          | Utility / Common           |
| cfg80211, nl80211             | Configuration Interface    |

---

## Standard Interface Functions

### cfg80211_ops (kernel ↔ driver configuration interface)
`connect`, `disconnect`, `scan`, `add_key`, `del_key`, `set_channel`,
`set_wiphy_params`, `get_station`, `dump_station`, `set_pmksa`,
`remain_on_channel`, `mgmt_tx`

### mac80211_ops (mac80211 ↔ driver hardware callbacks)
`tx`, `start`, `stop`, `add_interface`, `remove_interface`, `config`,
`configure_filter`, `sta_add`, `sta_remove`, `set_key`, `ampdu_action`,
`hw_scan`, `sw_scan_start`, `sw_scan_complete`, `flush`

### netdev_ops (network stack ↔ driver)
`ndo_open`, `ndo_stop`, `ndo_start_xmit`, `ndo_get_stats64`,
`ndo_set_mac_address`, `ndo_set_rx_mode`

---

## Known Execution Context Patterns

| Path     | Typical Context Flow                                                     |
|----------|--------------------------------------------------------------------------|
| TX path  | process/softirq → `ndo_start_xmit` → driver TX queue → FW command       |
| RX path  | hardirq (ISR) → `napi_schedule` → softirq → `driver_poll` → `netif_receive_skb` |
| Scan     | process (cfg80211 wq) → driver scan → FW scan cmd → FW scan done event → `cfg80211_scan_done` |
| FW event | hardirq or workqueue (depends on bus: PCIe MSI vs SDIO)                  |
| Connect  | process → `cfg80211_connect` → driver → FW cmd → FW event → `cfg80211_connect_result` |

---

## WiFi-Specific Lock Semantics

| Lock           | Scope                                          | Type    |
|----------------|------------------------------------------------|---------|
| `rtnl_lock`    | Top-level network config; ifup/ifdown, scan    | mutex   |
| `wiphy_lock`   | Per-wiphy cfg80211 lock (replaces rtnl in 5.12+) | mutex |
| `local->mtx`   | mac80211 master; vif/sta add/remove            | mutex   |
| driver locks   | TX/RX queue access (spinlock), config (mutex)  | varies  |

### Mandatory Lock Ordering

```
rtnl_lock > wiphy_lock > local->mtx > driver_priv_lock
```

Any violation of this order is a **deadlock risk** and must be flagged.

Sleeping locks (mutex) must NOT be held in softirq or hardirq context.
`spin_lock` (non-BH) must NOT be used where softirq can preempt.

---

## Layer Hierarchy

```
[Kernel Standard Interface]   cfg80211 ops, mac80211 ops, .ndo_* callbacks
          |
          v
[Driver Entry Points]         cfg80211_ops, ieee80211_ops, netdev_ops impls
          |
          v
[Driver Internal Logic]       Internal function chains, state machines
          |
          v
[HW Abstraction Layer]        HAL / HIF layer
          |
          v
[Firmware Interface]          FW command / event interface
```

---

## DO

- DO use this file as a lookup reference — match function names and file names against the tables above.
- DO check the lock ordering constraint whenever you see multiple locks in a trace.
- DO annotate layer boundaries using the five-layer hierarchy.
- DO treat pattern-to-module mappings as hints, not rules.
- DO verify context-appropriate lock usage (no sleeping locks in atomic context).

## DON'T

- DON'T assume a file belongs to a module based solely on its name — always verify with symbol evidence.
- DON'T invent layer names not in the hierarchy above.
- DON'T skip the lock ordering check — deadlock risks are high-priority findings.
- DON'T treat cfg80211 and mac80211 as interchangeable — they are distinct kernel subsystems.
- DON'T assume all drivers use mac80211; some use cfg80211 directly (fullmac drivers).
