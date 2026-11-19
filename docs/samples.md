# Sample questions and answers

These are real answers from the running system, not mock-ups. They were produced by the V1 build through its own web API, signed in as the demo users, with a real model (`openai/gpt-oss-20b` on Groq, the cheapest chat model on the account). The data is the synthetic *Halcyon Group* world (seed 7), so the numbers can be checked against an independent calculation, and each one below was.

How to read an answer: the sentence is the model's wording, but the numbers in it must appear in the result table (otherwise code replaces the sentence with a template); the table comes from a query that code planned, verified and ran **as the signed-in user**; the notes underneath are written by code.

Run it yourself: `make demo`, sign in as `ana.analyst`, and ask the same questions. See the [README](../README.md).

## 1. The same question, two people, two sets of data

> **Question (both users):** How many booked transactions are there for each entity I can see?

| Signed in as | Entities they may see | Answer | Table |
|---|---|---|---|
| `ana.analyst` | HAL-DE | There are 776 booked transactions for entity HAL-DE. As of 2026-06-30. | `HAL-DE`: 776 |
| `ben.analyst` | HAL-UK | There are 763 booked transactions for entity HAL-UK. As of 2026-06-30. | `HAL-UK`: 763 |

**Checked:** an independent count over the generated world gives 776 for HAL-DE and 763 for HAL-UK. Neither user is told the other entity exists: the database's row-level security decides what is visible, not the application.

Cost of Ana's answer: 3 model calls, 2,984 tokens in and 379 out, $0.00034.

## 2. Money is never added across currencies

> **Question (`ana.analyst`):** What is the total outflow by currency in June 2026?

**Answer:** The total outflow in June 2026 was -7924295.19 EUR and -97188.78 USD. As of 2026-06-30.

| currency | total_outflow |
|---|---|
| EUR | -7924295.19 |
| USD | -97188.78 |

**Checked:** June 2026 outflows for HAL-DE computed independently: EUR -7924295.19, USD -97188.78. Exact match. The verifier refuses any plan that would add EUR and USD together, so the answer is split by currency by construction.

Cost: 3 model calls, 3,409 tokens in and 387 out, $0.00037.

## 3. A ranked list, then a follow-up that remembers

> **Question 1 (`ana.analyst`):** Show me the five largest outflows.

**Answer:** The five largest outflows are: transaction T001104 of -1,952,935.66 EUR, T000562 of -1,899,392.37 USD, T000246 of -1,761,038.17 USD, T000225 of -1,755,696.70 EUR, and T001447 of -1,719,631.00 EUR. As of 2026-06-30.

| transaction_id | booking_date | account_id | amount | currency |
|---|---|---|---|---|
| T001104 | 2026-05-14 | A-DE-EUR-1 | -1952935.66 | EUR |
| T000562 | 2026-03-12 | A-DE-USD-1 | -1899392.37 | USD |
| T000246 | 2026-02-02 | A-DE-USD-1 | -1761038.17 | USD |
| T000225 | 2026-01-29 | A-DE-EUR-2 | -1755696.70 | EUR |
| T001447 | 2026-06-23 | A-DE-EUR-1 | -1719631.00 | EUR |

Notes written by code: Data as of 2026-06-30T23:00:00+00:00.; Showing the first 5 rows; more rows match.

**Checked:** the true five largest outflows for HAL-DE are T001104 (-1952935.66), T000562 (-1899392.37), T000246 (-1761038.17), T000225 (-1755696.70), T001447 (-1719631.00). Exact match, in order. (Outflows are negative amounts, so "largest" means most negative. An earlier build got this backwards and listed the *smallest* outflows; the catalogue now states the rule.)

> **Question 2, same conversation:** And the next 5?

The system carried the previous *plan* forward with an offset of 5 (not the previous rows), re-ran it, and verified it like any other query.

**Answer:** The next five transactions are: T000078 on 2026-01-12 for -1,621,536.56 USD, T000914 on 2026-04-23 for -1,551,775.54 EUR, T001461 on 2026-06-24 for -1,454,525.80 EUR, T000486 on 2026-02-27 for -1,308,039.86 EUR, and T000675 on 2026-03-25 for -1,299,639.41 EUR. As of 2026-06-30.

| transaction_id | booking_date | account_id | amount | currency |
|---|---|---|---|---|
| T000078 | 2026-01-12 | A-DE-USD-1 | -1621536.56 | USD |
| T000914 | 2026-04-23 | A-DE-EUR-2 | -1551775.54 | EUR |
| T001461 | 2026-06-24 | A-DE-EUR-1 | -1454525.80 | EUR |
| T000486 | 2026-02-27 | A-DE-EUR-2 | -1308039.86 | EUR |
| T000675 | 2026-03-25 | A-DE-EUR-2 | -1299639.41 | EUR |

**Checked:** the true outflows ranked 6 to 10 are T000078 (-1621536.56), T000914 (-1551775.54), T001461 (-1454525.80), T000486 (-1308039.86), T000675 (-1299639.41). Exact match.

Cost of the follow-up: 3 model calls, 4,457 tokens in and 447 out, $0.00047.

## 4. Asking again within five minutes

> **Question, twice in a row (`ana.analyst`):** How many booked transactions are there for each entity I can see?

First time: answered from the warehouse (There are 776 booked transactions for entity HAL-DE. As of 2026-06-30.). Second time the same rows came back, with this note added by code:

> Reused from your own identical query a moment ago, data as of 2026-06-30.

The reuse comes from that user's own per-user cache, which sits behind authentication and verification. Another user asking the same thing would be computed fresh for them (see sample 1: Ben's number differs). Second answer: There are 776 booked transactions for entity HAL-DE. As of 2026-06-30.

## 5. What it will not do

> **Question (`ana.analyst`):** Predict our cash position for next year.

**Answer (refused, reason `unsupported`):** I can't answer that from the governed treasury data.

The assistant answers only from governed data; forecasts are refused rather than invented.

> **Question (`ana.analyst`):** How much did we pay?

**Answer (clarify):** Could you specify the transaction, date range, or currency you are referring to?

A question that lacks something essential (which period, currency or entity) gets one short clarifying question instead of a guess.

## What went wrong on the way

Honest notes, because a sample set that only shows successes is a brochure.

- **"Largest outflows" first came back wrong** (the smallest outflows, then the largest *inflows*). The query was safe and verified, so no safety gate could catch it: it was a misreading of a signed column. Fixed by stating the ranking rule in the catalogue the planner reads, and measured here against the independent calculation.
- **The June question is not reliable on this small model.** Over six recorded attempts, in different conversations, it was answered correctly 3 times and the rest ended as a model-service error, an exhausted repair budget ("could not produce a verified answer") or a clarifying question nobody needed. Every outcome was safe: no wrong number was ever shown, because the verifier and the repair budget stop a bad plan. But a safe refusal is still a failed answer. Likely cause: the model sometimes writes an impossible date or a malformed plan. The planner now gets an explanation when the database rejects a value. Measuring this properly is what the gold set is for.
- **A repeated question once failed** with a model-service error. The cause: the provider reports a cut-off or non-conforming structured answer as an HTTP 400, and the adapter treated every 400 as final. It now recognises that case as an invalid response, which the orchestrator already repairs by asking again, and the planner has more room to finish its plan.
- **The eval baseline is real, the copilot eval is not recorded yet.** An unguarded model writing its own SQL scored 26% on the gold set, with 48% of items silently wrong. The copilot's own eval needs a full run that the free-tier token limit makes slow; the numbers above are a handful of hand-checked questions, not a benchmark.
