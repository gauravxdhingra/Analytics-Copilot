---
paths:
  - "packages/sql-verifier/**"
  - "services/query-api/**"
  - "mcp/analytics/**"
  - "packages/catalogue/**"
  - "data/catalogue/**"
---

# SQL verification, Query API and catalogue

Spec: FinAnalytics.md §6; ARCHITECTURE.md §8.2–8.3. The checklist in §8.3 is the implementation checklist for the reference Query API.

**Verifier (deterministic first):** `sqlglot`, Postgres dialect. Exactly one SELECT on one database. Reject identifiers not in the catalogue, sensitive columns, missing current-version filter, un-grouped money, mismatched date semantics, cross joins, non-allowlisted functions, catalog access (`pg_catalog`, `information_schema`, `pg_stats`). The LLM semantic check can only reject.

**Finance traps that must stay covered by tests:** summing across currencies, fan-out from one-to-many joins, wrong date field, closed vs half-open intervals, stale snapshot shown as current, rows cancelled after snapshot.

**Query API:**
- Single SELECT only, `READ ONLY` transaction, read-only role, `statement_timeout`, `lock_timeout`, row cap, schema allowlist.
- Runs as the requesting user (`SET LOCAL ROLE` / `SET LOCAL app.user_id` inside an explicit transaction). Row-level security is `FORCE`d. Views are `security_invoker`.
- Parameterized execution only. Map database errors to codes before they leave the service; raw errors go only to the restricted audit store.
- `numeric` maps to `Decimal`; set the session time zone explicitly.
- Multiple queries for one answer run in one `REPEATABLE READ` transaction.

**Catalogue:** one YAML per store (FinAnalytics.md §6.2), git-versioned, reviewed by a steward. Schema-drift CI must fail on undocumented or removed columns. Use `/catalogue-entry`.

Every verifier rule needs a positive and a negative test. A rule without a test is not a control.
