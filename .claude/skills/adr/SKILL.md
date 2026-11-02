---
name: adr
description: Create or supersede an Architecture Decision Record in docs/adr/. Use when a decision is made, an open [ADR] item from ARCHITECTURE.md §15 is resolved, or a non-negotiable needs changing.
argument-hint: "<short decision title>"
disable-model-invocation: true
---

Write an ADR for: $ARGUMENTS

1. Read `docs/adr/0000-template.md` and list `docs/adr/` to find the next number (zero-padded, 4 digits). If the decision is already in the ADR backlog (ARCHITECTURE.md §15) or the decisions table (FinAnalytics.md §16), read that entry and keep the D-number.
2. Create `docs/adr/NNNN-kebab-title.md` from the template. Fill every section:
   - **Context**: the forces, constraints and the spec sections involved (cite `§`).
   - **Options considered**: at least two real alternatives with honest trade-offs. Include the rejected ones that the spec already mentions.
   - **Decision**: one clear sentence, then the consequences.
   - **Verification**: how we will know it was right (a test, an eval, a metric, a review date).
   - Keep `[VERIFY]` and `[CONFIRM]` items that remain unresolved; do not invent facts about vendors or specs.
3. If it supersedes an ADR, set `Supersedes` and update the old one's status to `Superseded by NNNN`.
4. Add a row to the index in `docs/adr/README.md`.
5. If the decision changes either top-level doc, update FinAnalytics.md §16 and/or ARCHITECTURE.md §15 so they agree, then run the `spec-guardian` agent.
6. Status is `Proposed` unless the user said it is accepted. Do not commit.

Report the file path and the one-line decision.
