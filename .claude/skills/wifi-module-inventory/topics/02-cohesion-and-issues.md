# Topic 02 — Cohesion Rating & Issue Detection

Reference file for Module Inventory entry writing.
Use this when rating Internal Cohesion or detecting Issues for a module.

---

## Cohesion Rating Criteria

Choose exactly one rating per module. Justify your choice in the Internal
Cohesion paragraph.

### STRONG

All three conditions are true:
- **(a)** Files share at least one central data structure as their primary state
- **(b)** Functions in one file are directly called by other files in the module
- **(c)** The module has a clearly identifiable entry point or public API surface

Example: A module where all .c files operate on the same struct, call each
other's functions, and expose a single API header.

### MODERATE

Condition (a) is true, but (b) or (c) is only partial:
- Files access the same structs but mostly operate in parallel rather than
  forming a pipeline.
- Typical of "management" modules where each file handles a different aspect
  of the same resource.

Example: A power-management module where `pm.c`, `ps.c`, and `wow.c` all
access the device struct but rarely call each other.

### WEAK

Condition (a) is absent:
- Files are grouped because they deal with the same topic, but each could
  be moved to another module with minimal impact.
- Usually a sign that the module boundary should be reconsidered.

Example: A "utility" module containing unrelated helper functions that
share no data structures.

---

## Issue Labels — Detection Criteria

Report issues only when detection criteria are met. Each issue item must
name the affected files and explain the consequence.

### [SRP VIOLATION]

Trigger when:
- A single file contains functions belonging to two distinct responsibility
  domains (as defined by Rule 1: Single Responsibility).
- OR the module's responsibility sentence requires "and" between unrelated concerns.

Example:
> `[SRP VIOLATION]` rate_control.c handles both rate selection and TX retry
> logic. These are distinct responsibilities. Risk: changes to retry policy
> require touching rate selection code.

### [WEAK COHESION]

Trigger when:
- The cohesion rating is WEAK.
- OR more than 30% of files in the module have no direct symbol relationship
  to any other file in the module (no shared structs, no cross-file calls).

Example:
> `[WEAK COHESION]` 3 of 7 files (util_math.c, util_time.c, util_string.c)
> share no data structures or function calls with each other. Each could be
> moved independently.

### [HIGH COUPLING]

Trigger when:
- The External Dependencies table has **5 or more rows** (excluding
  Utility/Common).

Example:
> `[HIGH COUPLING]` This module depends on 6 other modules. Consider whether
> some dependencies can be replaced with callbacks or interfaces to reduce
> coupling.

### [CIRCULAR DEP]

Trigger when:
- Module A lists Module B as a dependency AND Module B lists Module A.
- OR a cycle of length 3+ exists (A→B→C→A).

Detection: After writing all module entries, build the inter-module dependency
graph and check for strongly connected components.

Example:
> `[CIRCULAR DEP]` MAC Management depends on Firmware Interface (for
> wmi_send_cmd), and Firmware Interface depends on MAC Management (for
> mlme_event_handler). Break the cycle by using a callback registration
> pattern.

### [INFORMATION LEAK]

Trigger when:
- A public-header file exposes structs or typedefs that are only meaningful
  within the module's internal implementation.
- Test: Does any external module need to allocate or inspect the struct
  directly? If not, it should be in an internal header.

Example:
> `[INFORMATION LEAK]` ieee80211_i.h exposes ieee80211_mgd_data to all
> includers. Only MAC Management files need this struct's internals.

---

## DO

- DO apply exactly one cohesion rating per module: STRONG, MODERATE, or WEAK.
- DO justify the rating by referencing conditions (a), (b), (c) in your paragraph.
- DO check every detection criterion before reporting an issue.
- DO name specific files and functions in every issue report.
- DO check for circular dependencies after all modules are written.

## DON'T

- DON'T invent cohesion ratings beyond STRONG, MODERATE, WEAK.
- DON'T report an issue without meeting the detection criteria above.
- DON'T use vague language like "could be better" — name the specific problem.
- DON'T count Utility/Common in the HIGH COUPLING threshold.
- DON'T report WEAK COHESION for a Utility/Common module — it is expected to
  have weak cohesion by nature.
