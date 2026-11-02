---
paths:
  - "data/eval-sets/**"
  - "data/golden-queries/**"
  - "**/evals/**"
  - "data/synthetic-generator/**"
---

# Evals and the synthetic world

Spec: FinAnalytics.md §4 and §11.

- Gold sets are authored **independently of the system** and pinned to a synthetic-world version. Never derive expected answers by running the system under test.
- Held-out sets are never used for prompt tuning. Do not read them while iterating on prompts.
- Numeric and SQL correctness are checked programmatically, never by an LLM judge. Judge metrics need calibration on ~100 human labels and a reported Cohen's kappa.
- Report results with denominators and bootstrap confidence intervals.
- Every production or demo failure becomes a regression case, tagged with a root cause (catalogue gap, grounding, planning, verifier, narrative, safety).
- Trap cases (cross-currency, fan-out, wrong date field, restricted scope, stale snapshot, unanswerable) must exist for every new store.
- The synthetic generator is deterministic from a seed and emits a manifest (counts, fault rates, label files). A world change bumps the version and invalidates pinned gold sets deliberately.
- Synthetic data only. No real customer, bank or employer data. Valid-looking IBANs must pass the mod-97 check, and use reserved or fictional institutions.
