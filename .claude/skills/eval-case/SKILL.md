---
name: eval-case
description: Add a regression or gold-set case to the eval sets from a failure, a trap, or a new requirement. Use when a wrong answer, leak, or missed detection is found.
argument-hint: "<capability> <short description of the failure or trap>"
---

New eval case: $ARGUMENTS

Read FinAnalytics.md §11 and `.claude/rules/evals.md` first.

1. **Classify**: capability (insights, recon, sentinel, safety, intent, rag), category (single-store, join, time comparison, ambiguity, trap, unanswerable, injection, leak), and the **root-cause tag** (catalogue gap, grounding, planning, verifier, narrative, safety).
2. **Author the expected result independently.** Take it from the synthetic world's ground-truth labels or hand-written reference SQL on the pinned world version. Never copy the system's current output as the expected answer.
3. **Write the case** in `data/eval-sets/<capability>/` using the existing item format (look at a neighbouring file first). Required fields: id, world version, question or input, reference SQL/result or expected behaviour, grain, date field, currency policy, category, root-cause tag, provenance (who/what found it, date).
4. **Check duplicates** against existing cases by grep before adding.
5. **Decide split**: regression cases go in the regression set. Do not add anything to the held-out set during prompt tuning.
6. **Prove it**: run the case against the current system and confirm it fails for the stated reason (for a regression) or passes/refuses as specified (for a trap). Report the result.
7. Name the release gate it feeds (FinAnalytics.md §11.5).
