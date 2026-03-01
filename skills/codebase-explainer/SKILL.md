---
name: codebase-explainer
description: >
  Explain and analyze the Samsung SCSC WLAN PCIe kernel driver codebase.
  Provides architectural overview, subsystem maps, data/control flow analysis,
  and file-level guidance to other skills or agents. Use when asked to
  "explain the codebase", "analyze architecture", "how does X work",
  "what does this file do", "show me the data flow", "describe the driver",
  or any question about understanding the SCSC WLAN driver structure.
  Also trigger for "codebase overview", "driver architecture", "subsystem map".
---

# SCSC WLAN PCIe Driver — Codebase Explainer Skill

This skill provides comprehensive architectural knowledge of the Samsung SCSC
(Smart Connectivity SoC) WLAN PCIe kernel driver used in Samsung Galaxy devices
(e.g., S25FE). The driver implements an 802.11 a/b/g/n/ac/ax/be wireless LAN
interface over PCIe, communicating with Maxwell firmware via shared memory.

## Quick Reference

- **Codebase root**: The workspace directory (typically `drivers/net/wireless/pcie_scsc/`)
- **Language**: Linux kernel C (GPL-2.0)
- **Module name**: `scsc_wlan.ko` (main), `scsc_wifilogger.ko` (logging)
- **Total**: ~263 source files, ~145K lines of code
- **Architecture doc**: `<skill_path>/ARCHITECTURE.md` (detailed reference)

---

## How To Use This Skill

### 1. Full Architecture Briefing

Read `<skill_path>/ARCHITECTURE.md` for complete driver architecture including:
- Layer diagram, core data structures, TX/RX flows, MLME signal patterns
- HIP shared memory layout, SAP routing, cfg80211 integration
- Init/shutdown sequences, error recovery, config symbols

### 2. Answering "How does X work?"

Use the subsystem map below to locate the right files, then read them.
Cross-reference with the call chain diagrams in `ARCHITECTURE.md`.

### 3. Providing Context to Other Skills/Agents

When another skill or agent needs codebase context, provide:
1. The **layer diagram** (Section 1 of ARCHITECTURE.md)
2. The **relevant subsystem files** from the map below
3. The **key data structures** involved (slsi_dev, netdev_vif, slsi_peer)

---

## Layer Architecture (Top to Bottom)

```
User Space (wpa_supplicant, hostapd, Android Wi-Fi HAL)
    |
    v
+---------------------------------------------------------+
| Linux cfg80211 / nl80211 Wireless Subsystem             |
+---------------------------------------------------------+
| cfg80211_ops.c — NL80211 operation handlers             |
+---------------------------------------------------------+
| MLME Layer          | Data Path    | Vendor/NAN/IOCTL   |
| mlme.c (control)    | tx.c (TX)    | nl80211_vendor.c   |
| mgt.c (mgmt utils)  | rx.c (RX)    | ioctl.c            |
|                     | netif.c      | nl80211_vendor_nan.c|
+---------------------------------------------------------+
| SAP Layer — Signal routing (4 Service Access Points)    |
| sap_mlme.c | sap_ma.c | sap_dbg.c | sap_test.c        |
+---------------------------------------------------------+
| HIP Layer — Host Interface Protocol (shared memory)     |
| hip.c + hip4.c (default) or hip5.c (newer SoCs)        |
| mbulk.c (memory bulk containers)                       |
+---------------------------------------------------------+
| Connection Manager Interface (cm_if.c)                  |
| scsc_mx / Maxwell firmware over PCIe                    |
+---------------------------------------------------------+
```

---

## Subsystem File Map

### Core Device Management
| File | Purpose | Key Structures |
|------|---------|----------------|
| `dev.h` | Master header — all core structs | `slsi_dev`, `netdev_vif`, `slsi_peer` |
| `dev.c` | Module init, device attach/detach | `slsi_dev_attach()`, `slsi_dev_detach()` |
| `netif.c` | Network interface ops, queue mgmt | `slsi_netif_register()`, NAPI setup |
| `netif.h` | Netif declarations, queue indices | Queue layout defines |

### cfg80211 Integration
| File | Purpose |
|------|---------|
| `cfg80211_ops.c` | All nl80211 ops: connect, scan, AP start, key install (6100 lines) |
| `cfg80211_ops.h` | Operation table declarations |

### Control Path (MLME/FAPI)
| File | Purpose |
|------|---------|
| `mlme.c` | MLME request builders: connect, scan, add_vif, set_key (8114 lines) |
| `mlme.h` | MLME function prototypes |
| `fapi.h` | Firmware API signal IDs, header structures, helper macros (7199 lines) |
| `mgt.c` | Management utilities: security, rates, EAPOL handling (10941 lines) |
| `mgt.h` | Management function prototypes |

### Data Path
| File | Purpose |
|------|---------|
| `tx.c` | TX data path: classify, enqueue, EAPOL special handling |
| `tx.h` | TX function prototypes |
| `tx_api.h` | TX API interface (when CONFIG_SCSC_WLAN_TX_API=y) |
| `txbp.c` | TX backpressure / per-VIF flow control |
| `rx.c` | RX indication handlers: data, mgmt frames, scan results (7500 lines) |
| `ba.c` | Block Acknowledgment RX reorder buffer |
| `ba.h` | BA structures and defines |

### HIP (Host Interface Protocol)
| File | Purpose |
|------|---------|
| `hip.c` | HIP abstraction: SAP registration, frame transmit routing |
| `hip.h` | HIP state machine, `slsi_hip` structure |
| `hip4.c` | HIP4 implementation: queues, shared memory, BH handler (3415 lines) |
| `hip4.h` | HIP4 config, queue layout, memory pool defines |
| `hip5.c` | HIP5 implementation for newer SoCs (3899 lines) |
| `hip5.h` | HIP5 structures |
| `mbulk.c` | Memory Bulk container: alloc/free/access shared memory slots |
| `mbulk.h` | MBULK structure and pool defines |

### SAP (Service Access Points)
| File | Purpose |
|------|---------|
| `sap.h` | SAP interface: `struct sap_api`, SAP IDs (MLME=0, MA=1, DBG=2, TST=3) |
| `sap_mlme.c` | Control signal RX handler — routes MLME indications to rx.c handlers |
| `sap_ma.c` | Data signal RX handler — routes MA-UNITDATA to rx.c, handles TX done |
| `sap_dbg.c` | Debug signal handler |
| `sap_test.c` | Test signal handler |

### Connection Manager
| File | Purpose |
|------|---------|
| `cm_if.c` | WLAN ↔ SCSC core bridge: service open/start/stop/close |
| `scsc_wifi_cm_if.h` | CM state machine, callback prototypes |

### MIB (Management Information Base)
| File | Purpose |
|------|---------|
| `mib.h` | All PSID defines, MIB encode/decode (14131 lines) |
| `mib.c` | MIB encode/decode implementation |
| `mib_text_convert.c` | Human-readable MIB conversion |

### Vendor Extensions
| File | Purpose |
|------|---------|
| `nl80211_vendor.c` | Vendor NL commands: GSCAN, roaming, RTT, LLS (8190 lines) |
| `nl80211_vendor.h` | Vendor command/event IDs |
| `nl80211_vendor_nan.c` | NAN (Neighbor Awareness Networking) vendor commands |
| `ioctl.c` | Private IOCTL handlers (10197 lines) |

### Features
| File | Purpose |
|------|---------|
| `tdls_manager.c` | TDLS peer-to-peer direct link setup/teardown |
| `traffic_monitor.c` | Throughput monitoring, dynamic tuning |
| `load_manager.c` | CPU load balancing for RX/TX (NAPI, IRQ affinity) |
| `cac.c` | Channel Availability Check (DFS) |
| `lls.c` | Link Layer Statistics |
| `local_packet_capture.c` | Local packet capture for debugging |
| `log2us.c` | Driver event logging to user space |
| `qsfs.c` | QoS Flow State |
| `reg_info.c` | Regulatory domain information |
| `conc_modes.c` | Concurrency mode management |
| `dpd_mmap.c` | Digital Pre-Distortion memory mapping |

### Debugging & Testing
| File | Purpose |
|------|---------|
| `debug.c` | Debug utilities |
| `debug_frame.c` | Frame debug printing |
| `procfs.c` | /proc filesystem entries |
| `udi.c` | Unified Debug Interface |
| `fw_test.c` | Firmware test commands |
| `src_sink.c` | Source/sink test traffic generation |
| `kunit/` | KUnit test suite |
| `test/` | Legacy unit tests |

### Platform & Logging
| File | Purpose |
|------|---------|
| `ini_config.c` | INI configuration file parsing |
| `panel_notifier.c` | LCD panel state notifications |
| `ril_notifier.c` | RIL (cellular) coexistence notifications |
| `dctas.c` | Direct C-TAS (Traffic Aware Scheduling) |
| `scsc_wifilogger_*.c` | Wi-Fi logger ring buffers |
| `osal/slsi_wakelock.h` | OS abstraction for wakelocks |

---

## Core Data Structures

### `struct slsi_dev` (dev.h)
The master device structure. One per physical device. Contains:
- `struct slsi_hip hip` — HIP instance
- `struct net_device *netdev[]` — Virtual interfaces (up to MAX_INTERFACES)
- `struct scsc_service *service` — Maxwell service handle
- `struct slsi_sig_send sig_wait` — Global MLME signal synchronization
- Recovery, debug, and feature state

### `struct netdev_vif` (dev.h)
Per-virtual-interface state (stored in `netdev_priv()`). Contains:
- `struct slsi_sig_send sig_wait` — Per-VIF signal sync
- `struct slsi_peer *peer_sta` — Connected peer (STA mode)
- `struct slsi_peer peer[]` — Peer table (AP mode)
- `enum nl80211_iftype iftype` — Interface type
- VIF activation state, scan state, power save config

### `struct slsi_peer` (dev.h)
Per-connected-device state. Contains:
- MAC address, AID, QoS/encryption state
- BA session buffers, buffered frames
- TX/RX statistics

---

## Key Data Flows

### TX: Host → Firmware
```
netdev hard_start_xmit → slsi_net_open_*()
  → tx.c: slsi_tx_data() — classify AC, lookup peer
    → hip.c: slsi_hip_transmit_frame() — MBULK alloc, signal prep
      → hip4/5: queue to shared memory, interrupt firmware
        → Firmware TX done → sap_ma txdone callback
```

### RX: Firmware → Host
```
Firmware interrupt → HIP BH handler
  → hip4/5: read RX queue from shared memory
    → sap routing: signal_id & 0xF000 → SAP class
      → SAP_MA: sap_ma handler → rx.c indication handlers
        → ba.c: reorder buffer (if BA session)
          → netif_rx() or napi_gro_receive() → Linux stack
```

### Control: MLME Request/Confirm/Indication
```
cfg80211 op (e.g., connect)
  → mlme.c: slsi_mlme_connect() — build FAPI signal
    → slsi_mlme_req() — transmit + wait for CFM (6s timeout)
      → HIP TX → Firmware processes
        → Firmware sends CFM → SAP_MLME → wake waiter
          → Firmware sends IND (async) → SAP_MLME → rx.c handler
            → cfg80211 notification to user space
```

---

## Signal Protocol (FAPI)

Signals follow a Request/Confirm/Indication pattern:

| Bits | Meaning |
|------|---------|
| `0xF000` | SAP class: MLME=0x2000, MA=0x1000, DBG=0x8000, TST=0x9000 |
| `0x0F00` | Type: REQ=0x0000, CFM=0x0100, RES=0x0200, IND=0x0300 |
| `0x00FF` | Signal-specific ID |

Common signals:
- `MLME-CONNECT.REQ/CFM` + `MLME-CONNECTED.IND` — Association
- `MLME-DISCONNECT.REQ/CFM` + `MLME-DISCONNECTED.IND` — Disassociation
- `MLME-ADD-SCAN.REQ/CFM` + `MLME-SCAN.IND` + `MLME-SCAN-DONE.IND` — Scanning
- `MA-UNITDATA.REQ` / `MA-UNITDATA.IND` — Data TX/RX

---

## HIP Shared Memory Layout (HIP4)

```
Offset     Pool          Size     Purpose
0x00000    CONFIG        8 KB     Queue config, firmware version
0x02000    MIB           32 KB    Device MIB data
0x0A000    TX_DAT        1 MB     Data TX MBULK slots (512 x 2KB)
0x10A000   TX_CTL        64 KB    Control TX MBULK slots (32 x 2KB)
0x11A000   RX            1 MB     RX MBULK slots (512 x 2KB)
```

6 Queues: FH_CTRL, FH_DAT, FH_RFB (host→fw), TH_CTRL, TH_DAT, TH_RFB (fw→host)

---

## Important Build Configurations

| Symbol | Effect |
|--------|--------|
| `CONFIG_SCSC_WLAN_HIP5` | Use HIP5 (newer SoCs: S5E9925+) vs HIP4 |
| `CONFIG_SCSC_WLAN_TX_API` | New TX API path vs legacy FCQ |
| `CONFIG_SCSC_WLAN_RX_NAPI` | NAPI-based RX vs interrupt-based |
| `CONFIG_SCSC_WLAN_LOAD_BALANCE_MANAGER` | CPU load balancing |
| `CONFIG_SCSC_WLAN_EHT` | WiFi 7 (802.11be) support |
| `CONFIG_SCSC_WIFI_NAN_ENABLE` | NAN protocol |
| `CONFIG_SCSC_WLAN_SUPPORT_6G` | 6 GHz band |
| `CONFIG_SCSC_WLAN_MAX_INTERFACES` | Max VIFs (default 5, max 16) |

---

## Workflow for Answering Questions

1. **"What file handles X?"** → Use the Subsystem File Map above
2. **"How does X flow work?"** → Read ARCHITECTURE.md Section for that flow
3. **"What struct holds X?"** → Check Core Data Structures section
4. **"How do subsystems interact?"** → Read Key Data Flows section
5. **"What config affects X?"** → Check Build Configurations table
6. **For detailed symbol lookup** → Use the `code-indexer` skill to search `indexing.md`
7. **For specific code reading** → Use Grep/Glob to find, then Read the source files
