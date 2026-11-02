---
paths:
  - "FinAnalytics.md"
  - "ARCHITECTURE.md"
  - "docs/**"
---

# Editing the spec and docs

- The two top-level docs cross-reference each other by section number and by decision id (D1–D11). When you renumber, add or remove a section or decision, fix every reference in both files.
- Keep the markers meaningful: **[DECIDE]**, **[TWEAK]**, **[VERIFY]**, **[CONFIRM]**. Do not delete one unless the underlying question is answered; if it is, record the answer in an ADR.
- Decisions live in FinAnalytics.md §16 and are mirrored as ADRs in `docs/adr/`. Changing a decision means a new ADR that supersedes the old one.
- Plain, direct prose. Tables for comparisons, mermaid for diagrams. No employer-specific names. The original source notes are private.
- Run the `spec-guardian` agent after non-trivial doc edits.
