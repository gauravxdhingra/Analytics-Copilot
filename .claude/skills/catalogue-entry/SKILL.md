---
name: catalogue-entry
description: Add or update a semantic-catalogue entry for a data store, with golden queries and eval cases. Use when onboarding a new table/view to the Insights capability or fixing a catalogue gap.
argument-hint: "<store name>"
---

Catalogue entry for: $ARGUMENTS

Read FinAnalytics.md §6.2 (catalogue format) and §6.5 (verifier), plus `.claude/rules/sql-and-query-api.md`.

1. **Inspect the source.** Use the Query API schema/freshness endpoints or the synthetic-world manifest to get real columns, types and keys. Do not guess columns. Ask the user for anything the data cannot tell you (business meaning, steward, sensitivity).
2. **Write `data/catalogue/<store>.yaml`** with: `store`, `definition`, `owner` (business + steward), `grain`, `current_row_filter`, `dates` (each with meaning and time zone), `money` (currency column, sign convention), `relationships` (with explicit cardinality), `dimensions`, `state_values`, `sensitivity` per column, `synonyms`, `known_issues`, `example_questions`.
3. **Glossary**: add any new business terms to the glossary file, one definition each.
4. **Golden queries**: add 3–5 verified question → QueryPlan → SQL triples in `data/golden-queries/`. Each must pass the verifier and match a hand-checked result on the pinned world.
5. **Eval cases**: invoke the `eval-engineer` agent (or `/eval-case`) to add traps for this store: cross-currency, fan-out through each declared relationship, wrong date field, restricted scope, stale snapshot, unanswerable.
6. **Drift check**: run the catalogue schema validation and the drift check if they exist; report undocumented columns.
7. Mark the entry `status: draft` until a steward reviews it. Do not commit.
