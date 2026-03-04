# Topic 02 — Module Entry Format

You are a subagent performing **Step 3: Module Entry Writing**.
Your input is the `module_boundaries.json` from Step 2, plus the scan data JSON.
Your output is the Markdown text for one or more module entries.

Also read: `topics/03-cohesion-and-issues.md` for cohesion rating criteria and issue labels.

---

## Two-Layer Entry Structure

Every module entry has exactly two layers: **Overview** and **Technical Detail**.
Both are mandatory. No fields may be empty or contain placeholders.

---

## Exact Format

```markdown
## <N>. <Module Name>

### Overview

**Responsibility**
One sentence starting with a verb that describes what this module does.
This sentence must be precise enough that a reader can determine whether
a random .c file belongs here or not.

**Role in the Driver**
Two to four sentences. Explain where this module sits in the overall driver
architecture: what calls into it, what it calls, and why it exists as a
separate module rather than being merged into a neighbor.

**Key Abstractions**
  - struct_name  : one-line description of its role
  - struct_name  : one-line description of its role
  (3 to 6 items)

---

### Technical Detail

**Files**

| File           | Type            | Responsibility                          |
|----------------|-----------------|-----------------------------------------|
| example.c      | core            | Main implementation of X                |
| example.h      | internal-header | Private structs shared within module    |

**Internal Cohesion**
<RATING>. One paragraph explaining how tightly the files are coupled.
Answer: Do they share a central data structure? Do they form a pipeline
or state machine? Are any files loosely attached?

**External Dependencies**

| Depends On          | What Is Used                                     |
|---------------------|--------------------------------------------------|
| Module Name         | specific_function() or struct_name used for X    |

If none: write "None (self-contained)."

**Issues** (omit entirely if no issues)

  - [LABEL] Description of the problem, affected files, and consequence.
```

---

## Field Rules

### File Type Values

| Value             | Meaning                                          |
|-------------------|--------------------------------------------------|
| `core`            | Primary implementation files                     |
| `interface`       | Files that implement an external API (cfg80211, etc.) |
| `helper`          | Utility functions used within the module         |
| `internal-header` | Headers used only within this module             |
| `public-header`   | Headers exposed to other modules                 |

### Responsibility Field

- Must start with a verb.
- Must be one sentence (no periods followed by more sentences).
- Must be specific enough to be a file membership test.

### Role in the Driver

- Must name at least one module that calls into this one, OR state it is a top-level entry.
- Must name at least one module this one calls, OR state it is a leaf module.
- Two to four sentences. Not one, not five.

### Key Abstractions

- List 3-6 items maximum.
- Each item: `struct/enum/callback_table name : one-line description`.
- Only list abstractions that are central to the module — not every struct it touches.

### External Dependencies Table

What counts as a dependency:
- `#include` of a header owned by a different module
- Direct function call to a function defined in a different module
- Access to a global variable or struct from a different module

What does NOT count:
- Kernel headers (`<linux/*>`, `<net/*>`)
- Includes of the driver's own Utility/Common module (list separately)
- Indirect dependencies (A→B→C does not make C a dependency of A)

### Issues Section

- Omit entirely if there are no issues. Never include an empty Issues section.
- Each issue uses one of five labels: `[SRP VIOLATION]`, `[WEAK COHESION]`,
  `[HIGH COUPLING]`, `[CIRCULAR DEP]`, `[INFORMATION LEAK]`.
- See `topics/03-cohesion-and-issues.md` for detection criteria.

---

## Full Example Entry

```markdown
## 3. MAC Management

### Overview

**Responsibility**
Manages the 802.11 connection state machine, including authentication,
association, roaming, and disconnection for all virtual interfaces.

**Role in the Driver**
MAC Management is the behavioral core of the driver. The Configuration
Interface module translates cfg80211 commands into requests that land here.
This module drives the connection lifecycle by sending commands to the
Firmware Interface module and reacting to events from it. No other module
is allowed to initiate a connection or authentication sequence — all such
logic is owned here.

**Key Abstractions**
  - ieee80211_sub_if_data  : per-vif state container (auth state, assoc state)
  - sta_info               : per-peer station record and capability cache
  - ieee80211_mgd_data     : managed-mode specific state (timers, retry counts)
  - cfg80211_connect_params : connect request parameters from userspace

---

### Technical Detail

**Files**

| File          | Type            | Responsibility                                |
|---------------|-----------------|-----------------------------------------------|
| mlme.c        | core            | Main MLME state machine, event dispatch       |
| auth.c        | core            | Authentication frame construction and parsing |
| assoc.c       | core            | Association request/response handling         |
| sme.c         | core            | SME layer: translates cfg80211 connect calls  |
| ibss.c        | core            | IBSS (ad-hoc) mode connection management      |
| ieee80211_i.h | internal-header | Private structs shared across module files    |

**Internal Cohesion**
STRONG. All five .c files operate on ieee80211_sub_if_data as their primary
state container, and form a clear sequential pipeline: sme.c receives the
connect request, auth.c handles the authentication exchange, assoc.c
completes association, and mlme.c owns the state machine that coordinates
all of them. ibss.c is slightly peripheral (ad-hoc only) but still operates
on the same central struct.

**External Dependencies**

| Depends On          | What Is Used                                         |
|---------------------|------------------------------------------------------|
| Firmware Interface  | wmi_send_cmd() to transmit auth/assoc frames via FW  |
| Configuration Iface | cfg80211_connect_result() to report outcome upstream  |
| Utility / Common    | Logging macros, timer helpers, memory allocation      |

**Issues**

  - [SRP VIOLATION] ibss.c mixes ad-hoc beacon generation with ad-hoc
    connection management. Beacon generation is a PHY/MAC-boundary concern
    and would more naturally live adjacent to the AP-mode beacon logic.

  - [INFORMATION LEAK] ieee80211_i.h exposes ieee80211_mgd_data in full
    to all files that include it, including files in Data Path and Scan.
    Only MAC Management files should need the internals of this struct.
```

---

## DO

- DO follow the exact two-layer structure: Overview first, then Technical Detail.
- DO write the Responsibility sentence first — it anchors the entire entry.
- DO name specific functions, structs, or headers in the External Dependencies table.
- DO use the exact File Type values from the table above.
- DO omit the Issues section entirely when there are no issues to report.
- DO write the Internal Cohesion paragraph with the rating word (STRONG/MODERATE/WEAK) first.

## DON'T

- DON'T add sections not in the format above (no "Summary", no "Metrics", no "Diagram").
- DON'T leave any field empty or write "TBD" / "TODO" as content.
- DON'T list more than 6 Key Abstractions — pick the most central ones.
- DON'T write a Responsibility sentence that requires "and" between unrelated concerns.
- DON'T include kernel headers in the External Dependencies table.
- DON'T include an empty Issues section — omit it entirely if clean.
- DON'T invent issue labels beyond the five defined ones.
