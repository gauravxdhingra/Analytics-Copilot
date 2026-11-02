---
name: stage
description: Plan a delivery stage (S0–S8) from FinAnalytics.md §14 into an ordered, test-first work plan with exit criteria. Use before starting a stage or a large capability.
argument-hint: "<S0..S8 or capability id, e.g. S3 or C1>"
---

Plan: $ARGUMENTS

1. Read FinAnalytics.md §14 (the stage row and its exit criteria), the capability sections it references, and the matching ARCHITECTURE.md sections. Read `docs/adr/` for decisions already made and `docs/stages/` for earlier plans. Do not read whole documents; grep for sections.
2. List open decisions this stage depends on (`[DECIDE]`, ADR backlog items). If a blocker has no ADR, the first task is `/adr`.
3. Write `docs/stages/<stage>.md` containing:
   - **Goal** and **exit criteria** copied verbatim from §14, plus the measurable targets from the capability sections.
   - **Work items** as small vertical slices, each with: description, spec section, owner agent (e.g. `test-engineer`, `eval-engineer`), the test or eval that proves it, and dependencies. Order so that tests/evals and baselines come first (principle 8).
   - **Risks** pulled from FinAnalytics.md §15 that apply here, and what we do about them.
   - **Definition of done** per item: tests pass, `/pre-pr` clean, docs and ADRs updated, trace spans present, threat model updated if the attack surface changed.
   - **Out of scope** for this stage.
4. Show the plan summary and ask the user to confirm before any implementation starts. Do not begin implementation in this skill.
