---
paths:
  - "**/*.py"
---

# Python rules

- Python 3.12, fully typed. `mypy --strict` must pass; no bare `Any` without a comment saying why.
- Pydantic v2 models at every boundary (tool args, graph state, API bodies). Use `model_validate`, never trust raw dicts.
- Money: `Decimal` and a currency code via `Money`. No `float` for amounts, rates or tolerances. Reconciliation solvers use integer minor units.
- Dates: half-open intervals; timezone-aware datetimes only; never `datetime.now()` in logic (inject a clock so tests and replay are deterministic).
- Async I/O for network calls; every call has an explicit timeout. No unbounded retries: use capped retries with jitter on idempotent reads only.
- No `print`; use structured logging with the trace id. Never log prompts, completions, tokens or restricted values.
- Dependencies are pinned in `uv.lock`. Adding a dependency needs a reason in the PR; prefer the standard library.
- Tests live beside the package under `tests/`. Use `hypothesis` for invariants (balances roll forward, no item matched twice, round-trips).
