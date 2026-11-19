# 0005. Memory and caching: per-user, user-written, and never trusted

- **Status:** Proposed
- **Date:** 2026-11-19
- **Deciders:** Owner
- **Supersedes:** none
- **Spec references:** Analytics Copilot.md §10.4, §10.3, D6, D8; ARCHITECTURE.md §5.2, §7, §8, §11

## Context

The slice answers each question on its own, so "and the next 5?" cannot work, nothing is remembered between sessions, and every repeated question pays for a fresh query and fresh model calls. The owner wants four kinds of memory (short-term, episodic, long-term and user-specific) and caching.

The spec already fixes the rules (§10.4, ARCHITECTURE.md §5.2): session memory has a TTL; long-term memory is opt-in, visible, editable and deletable, never stores restricted values, is tenant-isolated, and is untrusted at read time; caches are keyed per user because the copilot cannot know which users share a data scope. Non-negotiables 3 (the data layer owns authorization), 5 (untrusted content is data), 6 (fail closed) and 7 (everything traced) apply.

The main new risk is **memory poisoning**: anything written to memory from text the model read in the data (a remittance memo, a counterparty name) would carry an attacker's instruction into later sessions.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| A. Postgres for memory, an in-process TTL cache behind a port; memory written only from user input and code-built summaries | No new container on the laptop; same isolation primitives as the rest; poisoning ruled out by construction; port allows Valkey in the cloud profile | In-process cache is lost on restart and not shared across replicas |
| B. Valkey for everything now | Matches the cloud target | Another container to run, secure and test for a slice; no benefit on a laptop |
| C. Model-managed memory (the model decides what to remember and writes free text) | Flexible, "smart" | Persistent prompt injection; stores whatever the model read; hard to audit or delete |
| D. A shared or semantic result cache across users | Highest hit rate | Leaks data across scopes: forbidden by D6 |

## Decision

Use **A**.

- **Short-term (conversation) memory.** Per user and conversation: the user's question, the *query plan* the code produced, and a code-built summary (status, row count, data as-of date). Never data rows and never model prose. Expires after 24 hours. The last few turns go to the planner as hints, so a follow-up can reuse the previous plan.
- **Episodic memory.** The same turns, kept as the user's own history: listable, re-askable, deletable. Traces, the ledger and drafts remain the audit record and are not user-editable.
- **Long-term, user-specific memory.** Preferences and saved definitions the user writes through a visible panel. Short, validated text; no IBANs or long digit strings; never written by the model or from tool results; at most a handful per user; no expiry; deletable.
- **Read-time rule.** All memory reaches the model only inside an `<untrusted source="memory">` block and is described as hints. Preferences are re-checked against the current catalogue and the user's current entitlements; they cannot widen access.
- **Follow-ups.** The query plan gains an `offset`, allowed only with an `order_by` so paging is deterministic.
- **Result cache.** Inside the analytics tool server, after the token is validated and the SQL verified, before the Query API call. Key: tenant, user id, and a hash of the verified SQL, parameters and flags. Short TTL (default 5 minutes), bounded size per user. An entry stores the data's as-of date, and a hit is marked as cached in the answer. Errors are never cached.
- **Fail-soft, not fail-open.** If the memory store is down, the copilot answers without history. Memory is context, not a guardrail; nothing about authorization depends on it.
- All tables carry `tenant_id` and `user_id`, taken from the validated session, never from a request body.

## Consequences

- Positive: follow-ups work; repeated questions are cheaper and faster; users can see, edit and delete what is remembered; poisoning and cross-user leakage are closed by construction and by tests.
- Negative / costs: a new package and schema; an entitlement change can be visible for up to the cache TTL; the in-process cache is per replica.
- Follow-ups: a Valkey adapter for the cloud profile; multi-turn and leakage cases in the gold set once a real model run is affordable; retention schedules per artefact (§10.3) when more stores exist; a steward-review path for shared knowledge (not built).

## Verification

- Isolation tests: one user's turns, preferences and cache entries are never visible to another user or tenant.
- Poisoning tests: no tool result or model output is ever written to memory.
- A follow-up integration test: "the next 5" becomes the previous plan with an offset, and renders `OFFSET`.
- Cache tests: a hit skips the Query API but not authentication or verification; keys differ per user; entries expire.
- Browser tests: history, the memory panel, and deletion.

## Open items

- **[TWEAK]** TTLs (24 hours for turns, 5 minutes for the cache) and the cap on long-term entries.
- **[CONFIRM]** Whether saved definitions should later go through steward review before they shape plans.
