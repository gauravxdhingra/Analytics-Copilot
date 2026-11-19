# Stage V1: Vertical slice

Status: **Accepted** (2026-11-18). Depends on ADRs [0001](../adr/0001-vertical-slice-first-iteration.md), [0002](../adr/0002-model-port-and-gemini-adapter.md), [0003](../adr/0003-keycloak-oidc-token-exchange-and-rls.md) being accepted. Spec: Analytics Copilot.md §3, §6, §10, §12, §14; ARCHITECTURE.md §3, §5, §8, §10, §11, §14.

## Goal

One thin, real path through every layer, runnable with `make up` on a laptop: an authenticated user asks a treasury question in the web UI, the policy gateway authorizes the tool call, a verified parameterized query runs as that user under row-level security, the answer streams back with evidence and a "how I got this" view, a draft is created that only a different, step-up-authenticated approver can approve, and the whole run is visible as a trace. External systems are fakes behind real contracts; the model is behind a port.

## Exit criteria

Pulled from the V1 scope in ADR 0001, made measurable:

| Criterion | Check |
|---|---|
| Runs from a clean clone | `make up` then `make demo` pass; README quickstart verified on a fresh checkout |
| AuthN | OIDC login and logout through Keycloak; no access token in browser storage; MFA step-up on approval |
| AuthZ at three layers | Role check, gateway tool policy, database RLS each have a failing-then-passing test |
| Zero leaks | Cross-entity and cross-user leakage cases in the gold set: 0 leaks; cache isolation test passes |
| Deterministic gates | Verifier rejects every trap in the SQL trap set; a model cannot override a block |
| Eval gate | `make eval` produces a baseline report, then a report for the agent; release gate configured; replay hit rate 100% in CI |
| Traced | One trace id per run; span tree covers run, node, model call, tool call, verifier, human decision; no raw prompts in span attributes |
| Draft, never execute | No tool above `DRAFT` tier exists; the copilot credential cannot approve; drafter cannot approve own draft |
| Fail closed | With Keycloak, gateway or Query API stopped, every endpoint refuses with a mapped code |
| Quality gates | `make lint typecheck test` green locally and in CI (`ruff`, `mypy --strict`, secret scan, dependency audit) |

## Prerequisites

- ADRs 0001–0003 accepted. If the owner changes a decision, revise the ADR first.
- S0 items W5 (scaffold with `packages/schemas`), W6 (CI), W7 (pre-commit) are done or folded in as the first items below. The stack ADR (Python 3.12, `uv`, `ruff`, `mypy --strict`, React/TypeScript) is accepted; if not yet written, `/adr` it first.
- Tool installs on the laptop: `uv`, Docker, Node.

## Work items

Order puts checks and baselines before the agent (principle 8). Owners are agents or the user; "proof" is the check that must fail before the item is built.

### Foundation

**V1-1. Scaffold and schemas.** Layout per ARCHITECTURE.md §14, root `Makefile`, `packages/schemas` with `Money`, `Provenance`, `Evidence`, `Claim`, `AgentResult`. Owner: `test-engineer`. Proof: `Money` rejects `float` and cross-currency sums; models round-trip JSON; `Evidence` requires `source_ref`. Spec: §3. Depends on: stack ADR.

**V1-2. CI and pre-commit.** Lint, typecheck, tests, secret scan, dependency audit, static analysis, ADR index check. Owner: Claude, reviewed by `infra-reviewer`. Proof: a planted secret and a type error turn CI red. Depends on: V1-1.

### Data and identity

**V1-3. Synthetic world, small.** Halcyon Group: 2 legal entities, 2 currencies plus base, accounts, balances, transactions, FX rates, a few payment instructions; seeded generator with a fixed seed and a manifest; a handful of documents including two injection samples. Owner: Claude; `data-correctness-reviewer`. Proof: property tests for determinism and for balances reconciling to transactions in `Decimal`. Spec: §4. Depends on: V1-1.

**V1-4. Reference Query API with RLS.** FastAPI service implementing the contract in ARCHITECTURE.md §8.2 on Postgres: read-only, parameterized, `SET LOCAL ROLE` per request, RLS by entity, statement timeout and row caps, mapped error codes. Owner: `test-engineer` first. Proof: contract tests; a direct leakage test that user A cannot read entity B rows even with a hand-written query. Spec: ARCH §8. Depends on: V1-3, V1-5.

**V1-5. Keycloak realm and token exchange.** Realm as code with the four seeded users, clients per audience, token exchange permissions, step-up flow for approval. Proof: tokens with wrong audience, issuer or expiry rejected by a shared validation library; exchange yields a correctly scoped token. Spec: ARCH §5.1. Depends on: V1-1.

### Evaluation before the agent

**V1-6. Gold set v0 and traps.** About 30 questions with expected SQL and answers, plus traps: fan-out joins, currency mixing, date window boundaries, cross-entity access, injection in a document, ambiguous terms. Programmatic SQL result comparison first; DeepEval for narrative later **[VERIFY DeepEval API]**. Owner: `eval-engineer`. Proof: set validates against the world; each trap has a documented expected refusal or correction. Depends on: V1-3.

**V1-7. Baseline.** A naive text-to-SQL agent (no verifier, no catalogue) is run on the gold set and its report is stored. Owner: `eval-engineer`. Proof: report generated by `make eval`; release-gate thresholds set above the baseline. Depends on: V1-6, V1-8.

### Model port

**V1-8. ModelClient port and adapters.** Port, `FakeClient`, `ReplayClient`, `GeminiClient` per ADR 0002, with timeouts, retries, error mapping, usage and cost on spans. Owner: `test-engineer` first. Proof: shared contract suite passes on every adapter; import-boundary test forbids the SDK outside the adapter; replay miss fails the run. Depends on: V1-1.

### Platform

**V1-9. Policy gateway and analytics MCP server.** Versioned YAML policy (tier, role, entity scope, argument constraints); tools `list_stores`, `describe_store`, `resolve_business_term`, `run_validated_select`, `get_freshness`; each tool has schema, policy entry, contract test and trace span (use `/new-mcp-tool`). Proof: denied calls return mapped codes; gate decisions are not overridable by model output. Spec: §5, §6. Depends on: V1-4, V1-5.

**V1-10. Catalogue and verifier.** Small catalogue for the world (entities, grain, join keys, measures, currency rules); `QueryPlan` rendered to parameterized SQL in code with sqlglot; verifier checks for fan-out, mixed currency sums, window half-openness, missing filters, forbidden functions, row and cost caps. Owner: `data-correctness-reviewer`. Proof: every trap in V1-6 caught by the verifier; hypothesis tests on rendering. Spec: §6. Depends on: V1-6, V1-9.

**V1-11. Orchestrator.** LangGraph graph: route, plan, verify, execute, analyse, narrate, with run caps (`MAX_ITERATIONS`, `MAX_TOOL_CALLS`, cost, wall clock), input and output guardrails, `<untrusted>` wrapping for document text, evidence and provenance on every claim, and numbers computed in code. Owner: `test-engineer` first. Proof: gold set run passes the gate; injection corpus does not change tool calls; caps terminate runaway runs. Depends on: V1-7, V1-8, V1-10.

**V1-12. Tracing.** OpenTelemetry with Jaeger in Compose, span model per ARCHITECTURE.md §11, trace id returned to the UI, redacted prompts stored by span id in the decision store **[VERIFY OTel GenAI conventions status]**. Proof: a test asserts the span tree for a canned run and that no span attribute contains prompt text. Depends on: V1-9, V1-11.

**V1-13. Drafts and approval service.** `DRAFT`-tier tool creates a draft record; a separate approval service with its own audience, step-up, separation-of-duties check and an audit ledger; a failed ledger write denies the action. Proof: copilot token cannot approve; drafter cannot approve own draft; no tool executes a payment or config change. Spec: PL3 and non-negotiable 1. Depends on: V1-5, V1-9.

**V1-14. BFF.** Session cookie, OIDC code flow with PKCE, token refresh, token exchange to the orchestrator, SSE streaming, CSRF protection, rate limits. Proof: no token reaches the browser; stopped IdP leads to refusal. Depends on: V1-5, V1-11.

### Experience and delivery

**V1-15. UI.** React, TypeScript, Vite, Tailwind with shadcn/ui. Screens: login and logout; streaming chat; evidence chips with provenance; "how I got this" drawer (plan, SQL, verifier verdict, row counts, freshness); blocked and refused states with mapped codes; draft approval view; trace viewer. Tests: component tests and a Playwright path for the demo flow and the leakage demo (two users, same question, different data). Depends on: V1-12, V1-13, V1-14.

**V1-16. Compose, demo and README.** `make up`, `make down`, `make eval`, `make demo`; Docker Compose for Postgres, Keycloak, Query API, gateway, MCP server, orchestrator, BFF, approval service, UI, Jaeger. README with architecture diagram, quickstart, what is real and what is faked, and a short demo recording. Update CLAUDE.md "Commands". Proof: fresh-clone run. Depends on: all.

**V1-17. Close-out.** Update the threat model for the new trust boundaries (`/threat-model`), run `security-reviewer`, `data-correctness-reviewer`, `spec-guardian`, then `/pre-pr`; check the exit table. Depends on: V1-1–V1-16.

### Added after the first close-out

These were not in the original plan. They came from the reviewer agents (V1-17), from first use with a real model, and from the owner's request for memory and caching.

**V1-17. Close-out.** Four reviewer agents attacked the slice; every finding was reproduced and fixed test-first (fan-out through join chains, currency mixing through subqueries and ROLLUP, truncation, untrusted-text labelling, cancellation, draft summaries, unbounded logins). Threat model: `docs/threat-model/v1-vertical-slice.md`.

**V1-18. Second model provider.** A Groq adapter behind the same port, the cheapest chat model by default, one shared rule for choosing a provider, and a demo that makes real model calls when a key is set.

**V1-19. Correct ranking and honest row limits.** Signed amounts rank by magnitude of the outflow; the row-limit note says exactly what happened.

**V1-20 to V1-24. Memory and caching ([ADR 0005](../adr/0005-memory-and-caching.md)).** Per-user conversation memory (24-hour expiry), user-written long-term notes, a history view, a per-user result cache, and follow-ups through a plan offset. Storage in Postgres under forced row-level security; the cache sits behind authentication and verification; memory reaches a model only as a wrapped, capped, untrusted block; nothing is ever written to memory from a tool result or model output.

**Sample questions and answers** from a real run are in [docs/samples.md](../samples.md).

## Ordering

```
V1-1 ─┬→ V1-2
      ├→ V1-3 ─→ V1-4 ─┐
      ├→ V1-5 ─────────┼→ V1-9 ─→ V1-10 ─→ V1-11 ─→ V1-12 ─┐
      ├→ V1-8 ─────────┘             ↑                      ├→ V1-15 → V1-16 → V1-17
      └→ V1-6 → V1-7 (needs V1-8) ───┘                      │
                 V1-5, V1-9 → V1-13 ────────────────────────┤
                 V1-5, V1-11 → V1-14 ───────────────────────┘
```

Eval gold set and baseline (V1-6, V1-7) are done before the orchestrator (V1-11).

## Risks (Analytics Copilot.md §15) and what we do

| Risk | Response |
|---|---|
| Wrong numbers that look right | Verifier plus code-computed arithmetic plus programmatic result comparison in the gold set |
| Prompt injection through documents | `<untrusted>` wrapping, gateway as final gate, injection samples in gold set and red-team set |
| Cross-user or cross-entity leakage | RLS at the database, per-user cache keys, leakage cases as a zero-tolerance gate |
| Provider rate limits or drift | Replay adapter for CI; pinned model names; fallback mapped in the adapter |
| Keycloak token-exchange setup cost | Do V1-5 first and spike it before anything depends on it; fall back to a documented alternative only through an ADR |
| Scope creep from "production on glance" | Exit table is the contract; anything else goes to the backlog |
| Underlying system cannot enforce row scope **[CONFIRM]** | Not applicable to the reference Query API; noted in README as an integration assumption |

## Definition of done (per item)

- The proving test or eval fails before the item is built and passes after.
- `/pre-pr` is clean for the PR containing it.
- Docs and ADRs are updated; `spec-guardian` is clean.
- New tools have a policy entry, contract test and trace span.
- Threat model updated if the attack surface changed.
- No secrets, real data or employer-specific names. Commits are the owner's.

## Out of scope

Reconciliation (C3) and Sentinel (C4), the knowledge graph, document ingestion at scale, multi-tenancy beyond a tenant id in tokens, AWS and Azure deployment, load and chaos testing, the admin console and embeddable widget, multilingual support, and a real Bedrock or Foundry adapter.
