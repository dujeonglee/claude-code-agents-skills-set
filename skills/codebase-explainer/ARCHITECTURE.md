# SCSC WLAN PCIe Driver — Detailed Architecture Reference

> Samsung SCSC (Smart Connectivity SoC) WLAN kernel driver for Maxwell chipsets.
> Module: `scsc_wlan.ko` | ~263 files | ~145K lines | Linux kernel C (GPL-2.0)

---

## 1. System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                   User Space                                     │
│  wpa_supplicant / hostapd / Android Wi-Fi Framework / HAL       │
└─────────────────────────┬────────────────────────────────────────┘
                          │ nl80211 / cfg80211 netlink
┌─────────────────────────▼────────────────────────────────────────┐
│              Linux Wireless Subsystem (cfg80211)                 │
├──────────────────────────────────────────────────────────────────┤
│  cfg80211_ops.c — connect, scan, start_ap, add_key, etc.        │
├───────────────┬──────────────┬───────────────────────────────────┤
│  MLME Layer   │  Data Path   │  Extensions                      │
│  mlme.c       │  tx.c        │  nl80211_vendor.c (GSCAN, RTT)   │
│  mgt.c        │  txbp.c      │  nl80211_vendor_nan.c            │
│               │  rx.c        │  ioctl.c (private ioctls)        │
│               │  netif.c     │  lls.c (link layer stats)        │
│               │  ba.c        │                                  │
├───────────────┴──────────────┴───────────────────────────────────┤
│  SAP Layer — 4 Service Access Points (signal routing)           │
│  ┌───────────┬──────────┬──────────┬───────────┐                │
│  │ SAP_MLME  │ SAP_MA   │ SAP_DBG  │ SAP_TST   │                │
│  │ sap_mlme.c│ sap_ma.c │ sap_dbg.c│ sap_test.c│                │
│  └───────────┴──────────┴──────────┴───────────┘                │
├──────────────────────────────────────────────────────────────────┤
│  HIP Layer — Host Interface Protocol (shared memory queues)     │
│  hip.c → hip4.c (default) OR hip5.c (S5E9925+ SoCs)            │
│  mbulk.c — memory bulk container management                    │
├──────────────────────────────────────────────────────────────────┤
│  Connection Manager Interface (cm_if.c)                         │
│  ↕ scsc_mx API — service lifecycle over PCIe                    │
├──────────────────────────────────────────────────────────────────┤
│  Maxwell Firmware (runs on WLBT subsystem processor)            │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Data Structures

### 2.1 slsi_dev — Master Device (dev.h:~1889)

One instance per physical WLAN device. Holds all driver state.

```c
struct slsi_dev {
    /* Wireless/Network */
    struct wiphy              *wiphy;
    struct net_device         *netdev[CONFIG_SCSC_WLAN_MAX_INTERFACES];
    struct net_device         *netdev_ap;

    /* Maxwell / Firmware */
    struct scsc_mx            *maxwell_core;
    struct scsc_service       *service;
    struct slsi_hip            hip;              // HIP instance

    /* MLME synchronization */
    struct slsi_sig_send       sig_wait;         // Global signal wait
    struct mutex               start_stop_mutex;
    struct mutex               netdev_add_remove_mutex;

    /* Device state */
    atomic_t                   cm_if_state;      // CM interface state machine
    int                        recovery_level;
    bool                       mlme_blocked;

    /* Feature state */
    struct slsi_traffic_mon    traffic_mon;
    struct slsi_reg_info       reg_info;

    /* ... many more fields ... */
};
```

**Access pattern**: `sdev = slsi_get_sdev()` or from `ndev_vif->sdev`.

### 2.2 netdev_vif — Per-Interface State (dev.h:~1365)

Stored as `netdev_priv(net_device)`. One per virtual interface.

```c
struct netdev_vif {
    struct slsi_dev           *sdev;
    struct net_device         *wdev.netdev;
    struct wireless_dev        wdev;

    /* VIF identity */
    u16                        ifnum;           // Firmware VIF index
    u16                        vif_type;        // FAPI VIF type
    enum nl80211_iftype        iftype;          // STA, AP, P2P, etc.
    bool                       is_available;
    bool                       activated;

    /* Synchronization */
    struct mutex               vif_mutex;
    struct slsi_sig_send       sig_wait;        // Per-VIF signal wait

    /* Peers */
    struct slsi_peer          *peer_sta;        // STA mode: connected AP
    struct slsi_peer           peer[SLSI_PEER_INDEX_MAX]; // AP mode: stations
    int                        peer_sta_records;

    /* Scan */
    struct cfg80211_scan_request *scan_req;

    /* Mode-specific unions/data */
    struct {
        /* STA-specific fields */
        u8                     bssid[ETH_ALEN];
        u16                    beacon_int;
        /* ... */
    } sta;
    struct {
        /* AP-specific fields */
        struct sk_buff        *cache_beacon;
        /* ... */
    } ap;

    /* ... */
};
```

**Access pattern**: `ndev_vif = netdev_priv(dev)`.

### 2.3 slsi_peer — Connected Device (dev.h:~668)

```c
struct slsi_peer {
    /* Identity */
    u8                         address[ETH_ALEN];
    u16                        aid;              // Association ID
    bool                       valid;

    /* State */
    bool                       authorized;       // 802.1X port authorized
    bool                       qos_enabled;      // QoS/WMM capable
    u8                         uapsd;

    /* Data path */
    struct sk_buff_head        buffered_frames[SLSI_PEER_AID_MAX];
    struct slsi_ba_session_rx *ba_session_rx[NUM_BA_SESSIONS_PER_PEER];

    /* Association data */
    struct sk_buff            *assoc_ie;         // Assoc request/response IEs
    struct sk_buff            *assoc_resp_ie;

    /* Statistics */
    struct station_info        sinfo;
};
```

### 2.4 slsi_hip — HIP Instance (hip.h)

```c
struct slsi_hip {
    struct slsi_dev           *sdev;
    struct slsi_card_info      card_info;       // chip_id, fw_build, hip_version
    struct mutex               hip_mutex;
    atomic_t                   hip_state;       // STOPPED/STARTING/STARTED/STOPPING/BLOCKED
    struct hip_priv           *hip_priv;        // Implementation-private
    scsc_mifram_ref            hip_ref;         // Shared memory base reference
    /* HIP4 or HIP5 control structure (compile-time selected) */
    struct hip4_hip_control   *hip_control;     // (or hip5_hip_control)
};
```

### 2.5 sap_api — SAP Interface (sap.h)

```c
struct sap_api {
    u8   sap_class;                              // SAP_MLME=0, SAP_MA=1, SAP_DBG=2, SAP_TST=3
    u16  sap_versions[SAP_MAX_VER];
    int (*sap_version_supported)(u16 version);
    int (*sap_handler)(struct slsi_dev *sdev, struct sk_buff *skb);     // RX signal router
    int (*sap_txdone)(struct slsi_dev *sdev, ...);                     // TX completion
    int (*sap_notifier)(struct slsi_dev *sdev, unsigned long event);   // Event notification
};
```

---

## 3. TX Data Path (Host → Firmware)

### 3.1 Entry Point

```
Linux net_device hard_start_xmit callback
    ↓
slsi_net_open_*() [netif.c]  — selects TX handler based on config
```

### 3.2 TX Processing Chain

```
slsi_tx_data() [tx.c:~350]
│
├─ 1. Lookup peer from destination MAC
│     slsi_get_peer_from_mac() → returns slsi_peer or NULL
│
├─ 2. Classify Access Category (AC)
│     AC mapping: Voice(6,7) > Video(4,5) > BestEffort(0,3) > Background(1,2)
│
├─ 3. Special frame handling
│     ├─ EAPOL: slsi_tx_eapol() — priority path, MIC, encryption
│     ├─ ARP: flow control check (SLSI_ARP_UNPAUSE_THRESHOLD)
│     ├─ DHCP: tag for tracking
│     └─ Multicast: rate limiting
│
├─ 4. Flow control check
│     ├─ CONFIG_SCSC_WLAN_TX_API: txbp backpressure check
│     └─ Legacy: scsc_wifi_fcq queue check
│
├─ 5. Frame preparation
│     ├─ Add FAPI MA-UNITDATA.REQ header
│     ├─ Set colour (packet cookie for TX done tracking)
│     └─ Set VIF, peer_index, priority in header
│
└─ 6. Transmit to firmware
      slsi_hip_transmit_frame() [hip.c:~486]
      │
      ├─ Allocate MBULK from TX pool (TX_DAT or TX_CTL)
      ├─ Copy signal + payload into MBULK slot
      ├─ Enqueue to FH_DAT or FH_CTRL shared memory queue
      └─ Trigger firmware interrupt (doorbell)
```

### 3.3 TX Completion

```
Firmware processes frame, generates TX status
    ↓
TH_RFB queue: firmware returns MBULK slot
    ↓
HIP BH handler reads TH_RFB
    ↓
sap_ma_txdone() [sap_ma.c]
    ├─ Free MBULK slot back to pool
    ├─ Update peer TX stats
    └─ Unblock flow control if needed
```

### 3.4 TX Queue Layout (netif.h)

```
STA Mode Queues:
  Queue 0:     EAPOL (highest priority, never stopped)
  Queue 1:     Discard (internal use)
  Queue 2-5:   Reserved
  Queue 6:     ARP
  Queues 7-10: Per-AC unicast (VO, VI, BE, BK)

AP Mode Queues:
  Queue 0:     EAPOL
  Queue 1:     Discard
  Queues 2-5:  Broadcast/multicast per-AC
  Queue 6:     ARP
  Queues 7+:   Per-peer per-AC (each peer gets 4 queues)
```

---

## 4. RX Data Path (Firmware → Host)

### 4.1 Interrupt to Delivery

```
Firmware writes to TH_DAT queue + triggers interrupt
    ↓
HIP Bottom Half handler (workqueue or NAPI poll)
    ↓
hip4_irq_handler() / hip5_irq_handler()
    ↓
Read scoreboard: check TH_CTRL, TH_DAT, TH_RFB
    ↓
For each MBULK in TH_DAT / TH_CTRL:
    ↓
Extract FAPI signal header from MBULK
    ↓
Determine SAP class: signal_id & 0xF000
    ↓
hip_sap_cont.sap[class]->sap_handler(sdev, skb)
```

### 4.2 SAP_MA RX Handler (sap_ma.c)

```
sap_ma_rx_handler()
│
├─ MA-UNITDATA.IND (data frame)
│   → slsi_rx_data_ind() [rx.c]
│     ├─ Validate VIF and peer
│     ├─ A-MSDU deaggregation (if applicable)
│     ├─ PN (Packet Number) replay check
│     ├─ BA reorder buffer insertion [ba.c]
│     │   ├─ In-sequence: deliver immediately
│     │   └─ Out-of-sequence: buffer, timer flush
│     ├─ 802.11 → Ethernet header conversion
│     └─ Deliver to Linux:
│         ├─ NAPI: napi_gro_receive() [if CONFIG_SCSC_WLAN_RX_NAPI_GRO]
│         ├─ NAPI: netif_receive_skb() [if CONFIG_SCSC_WLAN_RX_NAPI]
│         └─ Legacy: netif_rx()
│
├─ MA-BLOCKACK-IND
│   → BA session setup/teardown notification
│
└─ Return RFB: replenish firmware RX pool via FH_RFB queue
```

### 4.3 SAP_MLME RX Handler (sap_mlme.c)

```
sap_mlme_rx_handler()
│
├─ CFM (Confirmation) signals:
│   → Wake up slsi_mlme_req() waiter via sig_wait completion
│
├─ IND (Indication) signals — routed to rx.c handlers:
│   ├─ MLME-SCAN.IND → slsi_rx_scan_ind()
│   ├─ MLME-SCAN-DONE.IND → slsi_rx_scan_done_ind()
│   ├─ MLME-CONNECT.IND → slsi_rx_connect_ind()
│   ├─ MLME-CONNECTED.IND → slsi_rx_connected_ind()
│   ├─ MLME-DISCONNECTED.IND → slsi_rx_disconnected_ind()
│   ├─ MLME-RECEIVED-FRAME.IND → slsi_rx_received_frame_ind()
│   ├─ MLME-ROAMED.IND → slsi_rx_roamed_ind()
│   ├─ MLME-MIC-FAILURE.IND → slsi_rx_mic_failure_ind()
│   └─ ... (many more)
│
└─ Error signals:
    → Trigger recovery if needed
```

---

## 5. Control Path — MLME Signal Protocol

### 5.1 FAPI Signal Format

```
FAPI Signal Header (in fapi.h):
┌─────────────────────────────┐
│ signal_id (u16)             │  ← SAP class + type + ID
│ receiver_pid (u16)          │
│ sender_pid (u16)            │
│ fw_reference (u32)          │
│ data_unit_descriptor (u16)  │
│ ... signal-specific IEs ... │
└─────────────────────────────┘

Signal ID encoding:
  Bits 15-12: SAP class (MLME=0x2, MA=0x1, DBG=0x8, TST=0x9)
  Bits 11-8:  Type (REQ=0x0, CFM=0x1, RES=0x2, IND=0x3)
  Bits 7-0:   Signal-specific identifier
```

### 5.2 Request/Confirm Pattern

```c
// Generic pattern used by all MLME operations:
int slsi_mlme_req(struct slsi_dev *sdev, struct net_device *dev, struct sk_buff *skb)
{
    1. Lock sig_wait
    2. Set expected CFM signal ID = (REQ signal_id | 0x0100)
    3. Transmit via HIP: slsi_hip_transmit_frame(skb, ctrl=true)
    4. Wait for completion: wait_for_completion_timeout(&sig_wait.completion, 6000ms)
    5. Check CFM result code
    6. Return success/failure
}
```

### 5.3 Key MLME Operations

| Operation | Function | Request Signal | Confirm | Async Indication |
|-----------|----------|---------------|---------|-----------------|
| Add VIF | `slsi_mlme_add_vif()` | MLME-ADD-VIF.REQ | MLME-ADD-VIF.CFM | — |
| Del VIF | `slsi_mlme_del_vif()` | MLME-DEL-VIF.REQ | MLME-DEL-VIF.CFM | — |
| Connect | `slsi_mlme_connect()` | MLME-CONNECT.REQ | MLME-CONNECT.CFM | MLME-CONNECTED.IND |
| Disconnect | `slsi_mlme_disconnect()` | MLME-DISCONNECT.REQ | MLME-DISCONNECT.CFM | MLME-DISCONNECTED.IND |
| Scan | `slsi_mlme_add_scan()` | MLME-ADD-SCAN.REQ | MLME-ADD-SCAN.CFM | MLME-SCAN.IND (per-BSS) + MLME-SCAN-DONE.IND |
| Start AP | `slsi_mlme_start()` | MLME-START.REQ | MLME-START.CFM | — |
| Set Key | `slsi_mlme_set_key()` | MLME-SETKEYS.REQ | MLME-SETKEYS.CFM | — |
| Set Power | `slsi_mlme_set_power_mode()` | MLME-POWERMGT.REQ | MLME-POWERMGT.CFM | — |
| MIB Set | `slsi_mlme_set()` | MLME-SET.REQ | MLME-SET.CFM | — |
| MIB Get | `slsi_mlme_get()` | MLME-GET.REQ | MLME-GET.CFM | — |

### 5.4 Association Call Chain (Detailed)

```
1. wpa_supplicant issues NL80211_CMD_CONNECT
      ↓
2. cfg80211_ops.c: slsi_connect()
   ├─ Validate parameters (SSID, BSSID, channel, security)
   ├─ Set MIBs: roaming, power save, security parameters
   └─ Call slsi_mlme_connect()
      ↓
3. mlme.c: slsi_mlme_connect()
   ├─ Allocate sk_buff for MLME-CONNECT.REQ
   ├─ Encode: BSSID, SSID, auth algorithm, cipher suites, IEs
   └─ Call slsi_mlme_req() — waits for CFM (6s timeout)
      ↓
4. HIP transmits MLME-CONNECT.REQ to firmware
      ↓
5. Firmware authenticates with target AP (802.11 auth + assoc)
      ↓
6. Firmware sends MLME-CONNECT.CFM → wakes slsi_mlme_req() waiter
      ↓
7. Firmware sends MLME-CONNECTED.IND (async, after association completes)
      ↓
8. sap_mlme.c routes → rx.c: slsi_rx_connected_ind()
   ├─ Extract: BSSID, SSID, channel, capabilities
   ├─ Create/update slsi_peer for connected AP
   ├─ Update netdev_vif state
   └─ Call cfg80211_connect_result() → notify user space
      ↓
9. wpa_supplicant receives connection result
   └─ Initiates 4-way handshake (EAPOL via tx.c)
```

---

## 6. HIP Layer — Host Interface Protocol

### 6.1 Shared Memory Layout (HIP4)

```
Base Address (from scsc_mx)
│
├─ CONFIG Pool (8 KB @ 0x0000)
│   ├─ Magic: 0xcaba0401
│   ├─ HIP version, config version
│   ├─ Queue descriptors (6 queues)
│   │   ├─ FH_CTRL: Host→FW control signals
│   │   ├─ FH_DAT:  Host→FW data frames
│   │   ├─ FH_RFB:  Host→FW RX refill buffers
│   │   ├─ TH_CTRL: FW→Host control signals
│   │   ├─ TH_DAT:  FW→Host data frames
│   │   └─ TH_RFB:  FW→Host TX completion refills
│   └─ Firmware version strings
│
├─ MIB Pool (32 KB @ 0x2000)
│   └─ Device MIB parameter storage
│
├─ TX_DAT Pool (1 MB @ 0xA000)
│   └─ 512 MBULK slots × 2 KB each (data frame TX)
│
├─ TX_CTL Pool (64 KB @ 0x10A000)
│   └─ 32 MBULK slots × 2 KB each (control signal TX)
│
├─ RX Pool (1 MB @ 0x11A000)
│   └─ 512 MBULK slots × 2 KB each (firmware RX delivery)
│
└─ [Optional] DPD Pool (512 KB)
    └─ Digital Pre-Distortion calibration data

Total: ~2.1 MB (HIP4), ~4.65 MB (HIP5)
```

### 6.2 MBULK Container (mbulk.h)

```c
struct mbulk {
    u16 next_offset;      // Chain link (for scatter-gather)
    u16 sig_bufsz;        // Signal buffer size
    u16 dat_bufsz;        // Data buffer size
    u16 len;              // Actual data length
    u8  flag;             // Flags (inline signal, chain, etc.)
    u8  clas;             // Class (data, control)
    u16 pid;              // Process/signal ID
    u16 colour;           // Packet cookie (for TX done matching)
    // ... followed by signal + payload data
};
```

### 6.3 Queue Operations

```
TX (Host → Firmware):
  1. slsi_hip_transmit_frame() gets MBULK slot from pool
  2. Copy signal header + payload into MBULK
  3. Write MBULK reference to queue ring buffer
  4. Update write pointer in shared memory
  5. Ring doorbell (interrupt firmware)

RX (Firmware → Host):
  1. Firmware writes MBULK ref to TH_DAT/TH_CTRL ring
  2. Firmware rings doorbell (interrupt host)
  3. HIP BH reads queue, processes each MBULK
  4. After processing, host returns slot via FH_RFB queue
```

---

## 7. Connection Manager Interface

### 7.1 State Machine (scsc_wifi_cm_if.h)

```
STOPPED ──► PROBING ──► PROBED ──► STARTING ──► STARTED
                                                  │
                                              (operations)
                                                  │
                                              STOPPING ──► REMOVED
```

### 7.2 Service Lifecycle (cm_if.c)

```
1. slsi_sm_wlan_service_open()
   ├─ scsc_mx_service_open() — get Maxwell service handle
   ├─ Map shared memory regions
   └─ Transition: PROBED → STARTING

2. slsi_sm_wlan_service_start()
   ├─ slsi_hip_start() — initialize HIP queues
   ├─ Load firmware MIB configuration
   ├─ Register SAP handlers
   └─ Transition: STARTING → STARTED

3. slsi_sm_wlan_service_stop()
   ├─ Disable all VIFs
   ├─ slsi_hip_stop() — drain queues
   └─ Transition: STARTED → STOPPING

4. slsi_sm_wlan_service_close()
   ├─ scsc_mx_service_close()
   ├─ Free resources
   └─ Transition: STOPPING → STOPPED
```

### 7.3 Recovery (cm_if.c)

```
Firmware Panic / Watchdog / HIP Error
    ↓
CM notifies driver: SCSC_WIFI_STOP or SCSC_WIFI_FAILURE_RESET
    ↓
slsi_sm_recovery_service_stop()
    ├─ Set hip_state = BLOCKED
    ├─ Cancel all pending MLME waits
    ├─ Notify all SAPs: sap_notifier(SCSC_WIFI_STOP)
    ├─ Clean up VIFs and peers
    └─ Stop HIP
    ↓
slsi_sm_recovery_service_close()
    └─ Release Maxwell service
    ↓
(Maxwell core resets firmware)
    ↓
slsi_sm_recovery_service_open() + start()
    ├─ Re-initialize HIP
    ├─ Restore interfaces
    └─ Resume normal operation
```

---

## 8. Locking Hierarchy

```
Level 1: sdev->start_stop_mutex         (driver start/stop)
  Level 2: sdev->netdev_add_remove_mutex  (VIF creation/deletion)
    Level 3: ndev_vif->vif_mutex            (per-VIF state)
      Level 4: sig_wait->send_signal_lock     (MLME signal send)
        Level 5: hip->hip_mutex                 (HIP operations)
          Level 6: mbulk pool locks               (memory allocation)

Rule: Always acquire in order (L1 → L6), never reverse.
```

---

## 9. Initialization Sequence

```
1. Module Load (dev.c: slsi_dev_load)
   ├─ Register service driver with SCSC core
   ├─ Register SAPs: sap_mlme, sap_ma, sap_dbg, sap_test
   └─ Register cfg80211 operations table

2. PCIe Probe / Device Attach (dev.c: slsi_dev_attach)
   ├─ Allocate wiphy + slsi_dev
   ├─ Initialize mutexes, workqueues
   ├─ Pre-allocate HIP structures: slsi_hip_pre_allocate_hip_priv()
   ├─ Create default network interfaces (STA, AP, P2P)
   ├─ Register with cfg80211: wiphy_register()
   └─ Register with CM interface

3. Service Start (cm_if.c: triggered by SCSC core)
   ├─ Open Maxwell service: scsc_mx_service_open()
   ├─ Map shared memory for HIP
   ├─ Initialize HIP: slsi_hip_init()
   ├─ Start HIP: slsi_hip_start()
   ├─ Load MIB configuration from files
   ├─ Set firmware parameters via MLME-SET
   └─ Enable network interfaces

4. Ready for Operation
   └─ cfg80211 ops active (scan, connect, etc.)
```

---

## 10. Shutdown Sequence

```
1. Service Stop Request
   ├─ Disable all network interfaces
   ├─ For each active VIF:
   │   ├─ Disconnect/stop AP
   │   ├─ Delete peers
   │   └─ MLME-DEL-VIF.REQ
   └─ Stop traffic monitors

2. HIP Shutdown
   ├─ Set hip_state = STOPPING
   ├─ Drain all queues
   ├─ Wait for pending TX completions
   └─ Set hip_state = STOPPED

3. Service Close
   ├─ Unmap shared memory
   ├─ Release Maxwell service
   └─ Free HIP structures

4. Device Detach
   ├─ Unregister network interfaces
   ├─ Unregister wiphy
   └─ Free slsi_dev
```

---

## 11. Feature Subsystems

### 11.1 Block Acknowledgment (ba.c/ba.h)

- Per-peer, per-TID BA sessions (up to 8 TIDs per peer)
- RX reorder buffer: window size up to 1024
- Timer-based flush for missing frames (timeout ~100ms)
- Handles: setup, teardown, window advance, duplicate detection

### 11.2 Traffic Monitor (traffic_monitor.c)

- Periodic throughput sampling (configurable interval)
- Threshold-based notifications for:
  - Power management adjustments
  - CPU frequency scaling hints
  - IRQ affinity changes
  - Logging level changes

### 11.3 Load Manager (load_manager.c)

- Available when `CONFIG_SCSC_WLAN_LOAD_BALANCE_MANAGER=y`
- Controls: NAPI scheduling, IRQ affinity, RPS configuration
- Balances RX processing across CPU cores
- Monitors per-CPU load and adjusts

### 11.4 TDLS Manager (tdls_manager.c)

- Discovery: find direct-link-capable peers
- Setup: negotiate TDLS tunnel
- Teardown: clean up direct link
- Channel switching for TDLS traffic

### 11.5 Flow Control

**Legacy (scsc_wifi_fcq.c):**
- Per-peer, per-AC flow control queues
- Backpressure when firmware queues full
- Netdev queue start/stop based on FCQ state

**TX API (txbp.c):**
- Backpressure-based flow control
- Per-VIF budgets
- Dynamic MOD (moderation) adjustment

---

## 12. Debug & Diagnostics

### 12.1 procfs Entries (procfs.c)

```
/proc/driver/unifi0/
├─ ver           — Driver/firmware version
├─ sta_bss       — Connected BSS info
├─ big_data      — Enhanced statistics
├─ throughput    — Current throughput
├─ tcp_ack       — TCP ACK suppression stats
└─ ... (many more)
```

### 12.2 UDI — Unified Debug Interface (udi.c)

- Captures all FAPI signals between host and firmware
- Available via /dev/unifiX character device
- Used by Samsung debugging tools

### 12.3 Wi-Fi Logger (scsc_wifilogger_*.c)

Ring buffer-based logging for Android:
- Connectivity events ring
- Packet fate ring (TX/RX packet tracking)
- Wakelock tracking ring

---

## 13. Platform Integration

### 13.1 Supported SoCs (from Kconfig depends)

| SoC | Features |
|-----|----------|
| S5E9925 (Exynos 2200) | HIP5, TX_API, Load Balance |
| S5E9935 (Exynos 2300) | HIP5, TX_API, Load Balance |
| S5E9945 (Exynos 2400) | HIP5, TX_API, Load Balance, CP Coex, LPC, UWB Coex |
| S5E9955 (Exynos 2500) | HIP5, TX_API, Load Balance, Force Silent Recovery |
| S5E8835 | TX_API, CPU HP Monitor, Host DPD |
| S5E8845 | TX_API, CPU HP Monitor, Load Balance |
| S5E5515 | TX_API, HIP4, Delayed Sched Scan |

### 13.2 Android Integration

- `CONFIG_SCSC_WLAN_ANDROID` — Android-specific power management, wakelocks
- Vendor commands for Android Wi-Fi HAL (GSCAN, RTT, LLS, packet fate)
- `/efs/wifi/.mac.info` — Persistent MAC address storage
- Panel/RIL notifiers for coexistence

---

## 14. Build Configuration Reference

### Critical Architecture Choices

| Config | Default | Effect |
|--------|---------|--------|
| `CONFIG_SCSC_WLAN_HIP5` | y (9925+) | HIP5 protocol with larger memory, different queue layout |
| `CONFIG_SCSC_WLAN_TX_API` | y (9925+) | New TX path with txbp.c backpressure vs legacy FCQ |
| `CONFIG_SCSC_WLAN_RX_NAPI` | y | NAPI poll mode vs interrupt-driven RX |
| `CONFIG_SCSC_WLAN_RX_NAPI_GRO` | n | Generic Receive Offload in NAPI path |
| `CONFIG_SCSC_WLAN_LOAD_BALANCE_MANAGER` | y (9925+) | CPU load balancing for RX/TX |

### Feature Toggles

| Config | Default | Feature |
|--------|---------|---------|
| `CONFIG_SCSC_WLAN_EHT` | n | WiFi 7 (802.11be) |
| `CONFIG_SCSC_WLAN_SUPPORT_6G` | n | 6 GHz band support |
| `CONFIG_SCSC_WIFI_NAN_ENABLE` | n | NAN (Neighbor Awareness Networking) |
| `CONFIG_SCSC_WLAN_GSCAN_ENABLE` | y | Google Scan + vendor commands |
| `CONFIG_SCSC_WLAN_RTT` | n | Round-Trip Time measurement |
| `CONFIG_SCSC_WLAN_DUAL_STATION` | n | Dual STA interfaces |
| `CONFIG_SCSC_WLAN_HOST_DPD` | y (883x+) | Host-side DPD calibration |
| `CONFIG_SCSC_WLAN_MAX_INTERFACES` | 5 | Maximum virtual interfaces (5-16) |
| `CONFIG_SLSI_WLAN_LPC` | y (9945+) | Local Packet Capture |
| `CONFIG_SCSC_WLAN_TRACEPOINT_DEBUG` | n | Tracepoint-based debugging |
