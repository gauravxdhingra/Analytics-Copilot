# 0007. Outflows are answered as positive amounts through an explicit plan option

- **Status:** Proposed
- **Date:** 2026-11-23
- **Deciders:** project owner
- **Supersedes:** none
- **Spec references:** Analytics Copilot.md §6 (QueryPlan), §11 (evals); ADR 0006

## Context

Transaction amounts are signed: an outflow is negative. People ask "what was the total outflow" and expect a positive figure. The gold set `insights_v0` is written that way (GS-007, 008, 016–019, 021). The QueryPlan could only produce `SUM(amount)`, so a correct plan returned a negative number and failed. A recorded Sonnet 5.5 run showed this was the largest single cause of failed items (7 of 16), and it also produced silent wrong answers: the right magnitude with the wrong sign.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| A. Accept signed values in the scorer | No product change | Hides a real mismatch between what users ask and what is shown |
| B. Let the model write the negation | Flexible | The model would be doing arithmetic on money |
| C. Explicit `negate` on a money select item, rendered by code | Deterministic, verified, covered by tests | The model must choose to set it; a prompt rule guides it |

## Decision

Option C. `SelectItem.negate` flips the sign of a money column or its aggregate (`-SUM(x)`). Code renders it and the verifier checks the resulting SQL. It is rejected on a non-money column. The planner prompt (`planner@3`) tells the model to set it for "how much was paid" and for totals or sizes of outflows, and never for inflows or balances.

## Consequences

- Positive: outflow answers match what users ask; no model arithmetic.
- Negative / costs: the planner prompt changed, so recorded model calls for `planner@2` no longer match and the copilot cassette must be re-recorded.
- Follow-ups: rounding is now a sibling plan option (`round`, below); the column-matching contract is handled by a prompt rule, not by loosening the scorer. See `docs/eval-review.md`.

## Verification

Renderer tests (`packages/sql-verifier/tests/test_plan.py`) and a prompt test. Eval: outflow items GS-007, 008, 016–019 and 021 should pass on a re-recorded run, and silent-wrong answers should fall.

## Open items

- Confirm with a treasury owner that "outflow" should always be shown positive (a [CONFIRM] item).
