# Codebase Explainer Generator — To-Do

## Feature 1: Appendix Support for Project-Specific Knowledge

**Goal**: Allow project-specific domain knowledge (hardware specs, protocol descriptions,
domain terminology) to be incorporated into generated documentation as appendix documents.

### A. Input Side — User-Provided Knowledge Base (Phase 1)

- [ ] Add optional `explainer-context/` directory discovery in Phase 1
  - Users place domain knowledge files here before running the generator
  - Example structure:
    ```
    <workspace>/.claude/explainer-context/
      glossary-additions.md    # domain terms, acronyms
      hardware-overview.md     # HW specs, register maps
      protocol-notes.md        # protocol-specific knowledge
    ```
- [ ] Update `scripts/analyze.py` — add `--context-dir` flag to inventory user-provided
      knowledge base files into `analysis.json`
- [ ] Pass discovered context to all Phase 2–4 subagent spawns as additional input
- [ ] Update `topics/module-design.md` — instruct subagent to consider user-provided context
- [ ] Update `topics/data-structures.md` — instruct subagent to reference domain glossary
- [ ] Update `topics/calltrace.md` — instruct subagent to reference protocol/hardware notes

### B. Output Side — Appendix Generation Subagent (New Phase 4.5)

- [ ] Create `topics/appendix.md` — subagent instructions:
  - Read all generated docs + user-provided context files
  - Identify concepts needing deeper explanation
  - Generate `appendix-NN-<topic>.md` documents
- [ ] Update `SKILL.md` — insert Phase 4.5 between doc writing and verification
- [ ] Update `00-index.md` generation to include appendix section with links
- [ ] Output structure addition:
  ```
  ├── appendix-01-glossary.md          # extended glossary
  ├── appendix-02-hardware-model.md    # HW-specific context
  ├── appendix-03-protocol-spec.md     # protocol deep dive
  ```

---

## Feature 2: READER Agent — Documentation Quality Review

**Goal**: Introduce a review agent that reads generated docs from a first-time reader's
perspective and raises questions/suggestions for comprehension improvement.

**Key distinction from existing Phase 5 verification**:
- Verification checks *factual accuracy* (symbol exists? file path correct?)
- READER checks *comprehension quality* (is this clear? is anything missing?)

### Implementation

- [ ] Create `topics/reader-review.md` — subagent instructions:
  - Read each generated doc as if encountering the codebase for the first time
  - Evaluate along dimensions:
    - **Clarity**: Are explanations understandable without prior project knowledge?
    - **Completeness**: Are there unexplained jumps in logic or missing context?
    - **Assumed knowledge**: What terms/concepts are used but never defined?
    - **Consistency**: Do cross-references between docs align?
    - **Missing diagrams**: Would an ASCII diagram help a particular section?
    - **Reader questions**: "What triggers this flow?", "Why is this split into two modules?"
  - Output `reader-review.json` per document:
    ```json
    {
      "file": "03-signaling-layer.md",
      "suggestions": [
        {
          "section": "SAP Multiplexing",
          "category": "assumed_knowledge",
          "severity": "HIGH",
          "issue": "Term 'SAP' used 14 times but never defined",
          "suggestion": "Add definition: Service Access Point..."
        }
      ]
    }
    ```
- [ ] Update `SKILL.md` — insert Phase 5.5 (after verification, before finalization)
- [ ] Update Phase 6 — apply HIGH-severity READER suggestions automatically,
      flag MEDIUM for user review

### Synergy with Appendix Feature

- READER identifies knowledge gaps that may become appendix topics
- User-provided knowledge base reduces "assumed knowledge" findings
- READER can suggest moving inline explanations to appendix documents

---

## Notes

- Both features are **additive** — no restructuring of the existing 6-phase pipeline needed
- Appendix (Phase 4.5) and READER (Phase 5.5) are independent and can be implemented separately
- Recommend implementing Appendix first, then READER, as READER benefits from appendix context
