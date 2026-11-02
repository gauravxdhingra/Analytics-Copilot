# Analytics Copilot — Product Specification

**Analytics Copilot** is an enterprise treasury assistant. It is a global chatbot built into a treasury web application. It answers questions over governed data, explains how it got each answer, and drafts work for humans to approve. It reaches data and tools only through MCP servers. These sit behind a policy gateway, and every step is traced.

This is an **engineering showcase built as a full product, not a proof of concept**. It runs on a realistic synthetic treasury domain (§4) behind the same connector interfaces a real deployment would use.

| Document | Contents |
|---|---|
| This file | Product scope, capabilities, safety, evaluation, delivery stages |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, Query API contract, compute model, AWS (primary) / Azure (second) / laptop deployment |
| `docs/adr/` | Architecture decision records (to be created) |
| `Generative AI Treasury Management.md` | Original source notes. **Contains employer-specific names; keep out of the public repository.** |

Markers: **[DECIDE]** needs a decision; **[TWEAK]** is a sensible default; **[VERIFY]** check against current library or spec versions before building.

---

## Contents

1. [Product definition](#1-product-definition)
2. [Scope and capability map](#2-scope-and-capability-map)
3. [Engineering principles](#3-engineering-principles)
4. [The synthetic treasury world](#4-the-synthetic-treasury-world)
5. [Platform layer](#5-platform-layer)
6. [Core capability: Insights](#6-core-capability-insights)
7. [Deep capability: Reconciliation Resolver](#7-deep-capability-reconciliation-resolver)
8. [Deep capability: Payment Integrity Sentinel](#8-deep-capability-payment-integrity-sentinel)
9. [Backlog capabilities](#9-backlog-capabilities)
10. [Safety: guardrails, PII, security](#10-safety-guardrails-pii-security)
11. [Evaluation](#11-evaluation)
12. [Product engineering](#12-product-engineering)
13. [Governance and compliance evidence](#13-governance-and-compliance-evidence)
14. [Delivery stages](#14-delivery-stages)
15. [Risks](#15-risks)
16. [Open decisions](#16-open-decisions)
17. [Glossary](#17-glossary)

---

## 1. Product definition

**Problem.** Treasury teams depend on report developers for every new question. Generic text-to-SQL tools make finance-specific mistakes: summing across currencies, joins that multiply rows, the wrong date field, stale data shown as current. Operational work (reconciliation breaks, fraud alerts) is manual and poorly explained. Generic AI assistants are not safe here. They read attacker-controlled text (remittance lines, emails, PDFs), and they tend to act with too much authority.

**Vision.** One assistant in the treasury app that:
- answers with governed, verified data and shows its assumptions, data freshness and lineage;
- drafts work (match proposals, case files, dashboards) that humans approve;
- never executes financial actions;
- leaves a complete, replayable audit trail for every answer.

**Personas.**

| Persona | Primary jobs |
|---|---|
| Treasury analyst | Ad-hoc questions, variance explanations, dashboards |
| Cash manager | Positions, missing statements, liquidity by entity and currency |
| Treasurer / CFO office | Read-only insights and trends |
| Reconciliation analyst | Resolve unmatched items, review proposals |
| Fraud / controls analyst | Triage alerts, review case files |
| Auditor / control owner | Evidence for controls (backlog capability) |
| Platform admin | Catalogue, policies, models, evaluations, tenants |

**Non-goals.** These are requirement-level controls:
- no payment release, approval or creation;
- no configuration changes;
- no finalization of reconciliation matches;
- no write access to the warehouse;
- no legal, audit or credit opinions;
- no autonomous multi-step actions without a human approval step.

**Product KPIs.** Every KPI is reported with its denominator, period and baseline.

| KPI | Definition | Target [TWEAK] |
|---|---|---|
| Answer correctness | Execution accuracy on the independent gold set | ≥ 85% overall, ≥ 95% single-store |
| Silent-wrong rate | Answers presented as verified that do not match the reference | ≤ 2%; 0 on the trap set |
| Safety | Injection attack success rate; cross-scope or cross-tenant leaks | 0 / 0 |
| Latency | Question to verified answer | p50 < 20 s, p95 < 60 s |
| Trust | Positive feedback rate; correction rate | Tracked, improving release over release |
| Adoption | Weekly active users; % of questions answered without developer help | Tracked in the demo tenant |
| Cost | Model + tool + warehouse cost per answered question | Within budget [DECIDE] |

---

## 2. Scope and capability map

Depth beats breadth. The platform and **Insights** are built to production grade first. Then come two deep capabilities that show different AI patterns. The rest stays in a designed backlog.

| ID | Capability | AI pattern shown | Tier | Status |
|---|---|---|---|---|
| PL1 | Agent Policy Gateway | Tool-call security, taint tracking | Platform | **Core** |
| PL2 | Decision-Trace Layer | Agent observability, replay | Platform | **Core** |
| PL3 | Approval Service | Human-in-the-loop, maker-checker | Platform | **Core** |
| PL4 | Registry and Experimentation | Prompt/model/catalogue versioning, A/B, shadow | Platform | **Core** |
| C1 | Insights | Governed text-to-SQL, multi-agent, semantic layer | READ / ANALYZE | **Core** |
| C2 | Dashboard Composer | Multi-turn spec editing on top of C1 | DRAFT | Wave 2 |
| C3 | Reconciliation Resolver | LLM extraction + constraint solver + calibrated scoring | DRAFT | **Deep** |
| C4 | Payment Integrity Sentinel | Temporal graph + detectors + LLM investigator | DRAFT | **Deep** |
| B1 | Trade Confirmation Intelligence | Document AI | DRAFT | Backlog |
| B2 | Bank Fee Audit | Document AI + deterministic engine | DRAFT | Backlog |
| B3 | Bank Onboarding Factory | Closed-loop multi-agent with tests | DRAFT | Backlog |
| B4 | Configuration Impact Analyzer | GraphRAG | READ | Backlog |
| B5 | Audit Evidence Agent | Agentic RAG, reproducible evidence | DRAFT | Backlog |
| B6 | Regulatory Reporting Assembler | GraphRAG over regulation + lineage | DRAFT | Backlog |

**Dependencies.** PL1–PL4 come before every capability. C2 builds on C1. C3 uses C1's warehouse access and the Operations server. C4 uses the Operations server, the payment graph, and signals from PL1 (stripped hidden content) and B1 (confirmation SSI mismatches, when B1 is built).

**Shared assets built once:** the four MCP servers (Analytics, Operations, Knowledge, Drafts), the synthetic world (§4), the document pipeline, the graph store, the evaluation service (§11) and the registry (PL4).

---

## 3. Engineering principles

1. **Typed state and typed outputs.** Every graph node reads and writes Pydantic models. Validators coerce common model mistakes (stray wrappers, nested dicts, invented enum values).
2. **Deterministic caps in code.** `MAX_ITERATIONS`, `MAX_TOOL_CALLS`, `MAX_REPAIR_ATTEMPTS`, a cost budget and a wall-clock budget apply per run. The LangGraph `recursion_limit` is derived from these, never left at the default.
3. **Real timeouts and fallbacks** on every model and tool call: template answer, prior state, or "insufficient evidence".
4. **Evidence first.** Every claim carries `Evidence`. A verifier rejects unevidenced claims.
5. **The LLM never computes.** Money, dates, tolerances, fees, limits and matches are computed in Python or SQL. The model selects, explains and drafts.
6. **Provenance labels** on every statement: `SYSTEM_STATE`, `DOCUMENT`, `INFERENCE`, `UNKNOWN`.
7. **Action tiers** `READ`, `ANALYZE`, `DRAFT`. No mutate or execute tools exist.
8. **Untrusted content is data.** Remittance text, PDFs, emails and message payloads are tainted and shown to models only inside delimited data blocks.
9. **Evaluation before feature.** Each capability starts with its gold set and baseline. Every change is gated by evals.
10. **Everything is versioned.** Prompts, models, catalogue, policies and eval sets are versioned in the registry (PL4). Every trace records which versions produced it.

**Shared data objects:**

```python
class Provenance(str, Enum):
    SYSTEM_STATE = "system_state"; DOCUMENT = "document"
    INFERENCE = "inference";       UNKNOWN = "unknown"

class Evidence(BaseModel):
    evidence_id: str
    provenance: Provenance
    source_tool: str | None = None      # e.g. "ops.get_message"
    source_ref: str                     # object id, chunk id, query hash
    snapshot_at: datetime | None = None
    excerpt: str | None = None          # short, redacted
    trace_span_id: str | None = None

class Money(BaseModel):
    amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")   # never summed across currencies

class Claim(BaseModel):
    text: str
    evidence: list[Evidence]
    confidence: float = Field(ge=0, le=1)

class AgentResult(BaseModel):
    run_id: str
    capability: str
    claims: list[Claim]
    open_questions: list[str] = []
    drafts: list[str] = []
    caveats: list[str] = []             # freshness, grain, currency, missing data
    versions: dict[str, str]            # prompt, model, catalogue, policy versions
```

**Model role defaults [TWEAK]:**

All generative models are hosted (D3). Temperature applies where the model supports it.

| Role | Temperature | Reasoning effort |
|---|---|---|
| Planner / router | 0.1 | medium |
| Verifier / critic / adjudicator | 0.0 | high |
| Extraction | 0.0 | low–medium |
| Narrative | 0.2 | medium |
| Drafting | 0.15 | high |
| Synthetic / adversarial generation | 0.5–0.7 | medium |

---

## 4. The synthetic treasury world

The showcase is only as convincing as its data, so the synthetic world is a first-class product with its own tests and releases.

**The fictional company [DECIDE name].** A multinational group (working name *Halcyon Group*) with:
- 12–20 legal entities across 6–8 countries and their time zones and holiday calendars;
- 8–12 banks and 150–300 bank accounts;
- 1,000–3,000 counterparties with standing settlement instructions (SSIs);
- 8–10 currencies;
- 2 years of history.

**Generated objects (all linked and consistent):**
- Entities, accounts (valid IBAN/BIC check digits), counterparties, SSIs with effective dates, users and entitlements (roles, entity and account scopes).
- Cash transactions and movements with versions; payments with maker/checker/releaser events.
- Bank statements (MT940 and camt.053) whose entries correspond to payments, with realistic remittance text.
- FX and money-market trades with confirmations; bank fee statements (camt.086/CSV) with fee schedules.
- A document corpus: operating manuals, policies, bank implementation guides, runbooks and incident tickets that describe the generated configuration.

**Realism controls [TWEAK]:**
- seasonality (month-end and quarter-end peaks, payroll cycles);
- heavy-tailed amounts;
- per-bank remittance styles;
- per-counterparty netting habits;
- FX rate paths from a seeded stochastic process;
- business-day calendars per currency.

**Injected faults with ground-truth labels:**
- reconciliation: bank fees deducted, split and partial payments, netting, missing references, look-alike decoys;
- statements: duplicate and corrected statements, missing statement days;
- fraud: an SSI change followed by a first payment, structuring just under the approval limit, colluding approver pairs, dormant account reactivation, hidden-instruction documents;
- legitimate look-alikes (announced SSI migrations, year-end bulk payments) to measure false positives;
- data quality: late snapshots, cancelled-after-snapshot rows.

**Engineering requirements:**
- deterministic from a seed;
- scales from a laptop profile to roughly 50M rows;
- emits a manifest (counts, fault rates, label files);
- loads into the Postgres analytics databases (served through the Query API), the mock Operations API and the document store;
- property tests guarantee consistency (every statement entry traces to a payment or an injected fault; balances roll forward);
- versioned releases, because gold sets are pinned to a world version.

**Prompt-injection corpus.** Adversarial remittance lines, emails and PDFs (zero-width characters, white-on-white text, off-page text, instruction-style text), each labelled with the expected safe behaviour.

---

## 5. Platform layer

### 5.1 MCP servers

All four are internal-only, sit behind PL1, emit PL2 traces, and validate audience-bound user tokens themselves (ARCHITECTURE.md §5).

**Analytics (read-only):** `list_stores`, `describe_store`, `resolve_business_term`, `sample_values`, `run_validated_select(plan|sql, purpose, expected_grain)` (verifies before executing; returns capped rows, row count, query hash, snapshot id, warnings), `get_freshness`. All data access goes through the **Query API** (D4): a REST service that runs read-only SQL on the Postgres analytics databases as the requesting user. Its contract is in ARCHITECTURE.md §8.2.

**Operations (read-only):** `get_message`, `get_cash_transaction`, `get_cash_movement`, `get_trade`, `get_settlement_context`, `get_statement`, `get_statement_entries`, `get_reconciliation_items`, `get_confirmation`, `get_static_data`, `get_permissions`.

**Knowledge (read-only):** `search_docs` (hybrid BM25 + dense + cross-encoder rerank, with ACL filters applied inside the query), `get_doc_chunk`, `graph_query` (named, parameterized templates only), `similar_incidents`.

**Drafts (write to drafts only):** `create_draft`, `list_drafts`, `get_draft`, `submit_for_review`. It cannot approve.

**MCP feature use [VERIFY against the current spec revision]:**
- **Elicitation** for clarifying questions and filter confirmations, so the client renders them as structured quick replies.
- **Structured tool output** with output schemas, so results are typed end to end.
- **Resources** for catalogue entries and documents; **prompts** for reusable, versioned task templates.
- **Progress notifications** to stream stage updates ("store identified", "verifying query").
- **Streamable HTTP transport with OAuth** in production; stdio only for local development.
- Tool manifests are pinned and reviewed. Tool descriptions are treated as code, because a poisoned description is an attack vector.
- **A2A** (agent-to-agent protocol) is an optional extension point for exposing capabilities to external agents. It is not needed for the core.

### 5.2 PL1 — Agent Policy Gateway

A policy enforcement point between every agent and every MCP server.

**Pipeline:**
1. **Identify:** agent, user, tenant, run.
2. **Authorize:** the agent's tool allowlist and the user's capability. Data scope is not decided here; the underlying system enforces it (D6).
3. **Validate:** argument schema and value constraints.
4. **Taint check.**
5. **Budget:** calls, tokens, cost and wall clock.
6. **Forward** to the MCP server.
7. **Post-process:** redact, wrap untrusted fields, attach taint labels.
8. **Ledger** entry and trace span.

The result is either the tool output or `PolicyDenied(reason_code)`.

**Policy as code (versioned, reviewed, eval-gated) [TWEAK]:**

```yaml
version: 3
agents:
  insights.query_planner:
    tools:
      - analytics.describe_store
      - analytics.run_validated_select:
          constraints: { max_rows: 5000, max_runtime_s: 30, require_purpose: true }
  recon.remittance_extractor:
    tools: []                    # extraction agents get no tools
  recon.adjudicator:
    tools: [drafts.create_draft]
defaults:
  data_scope: inherit_user
  max_tool_calls_per_run: 40
  max_cost_usd_per_run: 2.00
  redact: [iban, account_number, person_name_in_free_text]
  tainted_sources: [statement.remittance, email.body, pdf.text, message.payload]
```

**Taint rules:**
1. Tainted values reach a model only inside `<untrusted source="…">` blocks.
2. A tool call that is chosen right after new tainted content arrives, and is not in the declared plan, is held for re-planning (plan-then-execute).
3. Tool arguments copied verbatim from tainted content are allowed for READ tools. They are blocked for DRAFT tools unless a deterministic validator confirms them.

**Hidden-content stripping:**
- What it removes: zero-width characters, HTML comments, CSS-hidden and off-screen text, white-on-white PDF text, and non-rendered PDF layers.
- The stripped content is logged and sent to C4 as a fraud signal.

**Ledger:** append-only, hash-chained Postgres table (`seq, ts, run_id, trace_id, tenant_id, agent_id, user_id, tool, args_hash, args_redacted, decision, reason_code, policy_version, result_hash, prev_hash, row_hash`).

**Failure behaviour:** fail closed. Every denial carries a reason code that is visible in traces.

**Evaluation:**
- red-team suite: 100% of known attacks blocked;
- attack success rate on a held-out injection set generated by a different model: 0;
- false-deny rate below 1%;
- p95 overhead below 30 ms (excluding NER).

### 5.3 PL2 — Decision-Trace Layer

OpenTelemetry across nodes, model calls, retrievals, tool calls, verifier checks and human decisions, under one trace id.

**Span model:**

| Span | Key attributes |
|---|---|
| `agent.run` | run, capability, user, tenant, graph / policy / prompt / catalogue versions |
| `agent.node` | name, iteration, state hashes |
| `gen_ai.chat` | model, temperature / effort, tokens, prompt template id + hash |
| `retrieval.search` | retriever, k, result ids, scores, rerank |
| `tool.call` | tool, server, policy decision, reason, args hash, rows |
| `verifier.check` | check name, passed, details |
| `human.decision` | accept / reject / edit, actor, draft id, edit size |

- Prompt and completion content goes redacted to the decision store, not into span attributes.
- Thread pools need explicit context propagation (`context.get_current()` / `context.attach()`).

**Replay:** inputs, redacted tool results, versions and seeds are stored, so any run can be replayed against recorded tool results. This is the basis of regression tests and incident analysis.

**Targets:** trace completeness ≥ 99.5%; replay fidelity ≥ 95% for temperature-0 roles. Tracing never blocks a run (async export, bounded queue).

### 5.4 PL3 — Approval Service

Every DRAFT output (match proposals, case files, dashboards, letters) passes through a real approval component:
- **Maker-checker.** The requester cannot approve their own draft. Approver groups are defined per draft type.
- **Segregation-of-duties rules** are evaluated at approval time against entitlements.
- **Step-up authentication** for sensitive draft types.
- **Typed review UIs** with a diff view, the draft's evidence, and its alternatives.
- **States** `DRAFT → PENDING_REVIEW → APPROVED | REJECTED | CHANGES_REQUESTED`, with an immutable history.
- **Approval events** are written to PL2 and become labelled training and evaluation data.
- **Downstream hand-off** goes through an `ApprovalSink` adapter (ticket, file export or webhook). In the showcase, approval records a decision and never touches a financial system.

### 5.5 PL4 — Registry and Experimentation

- **Registry** of versioned artefacts: prompts, model routes, the catalogue, policies, guardrail configurations, eval sets and synthetic-world versions. Each artefact has an owner, a changelog and its eval results.
- **Promotion flow:** change → offline eval gate → shadow traffic → canary → full rollout, with one-click rollback.
- **Experiments:** A/B tests and interleaving for prompt and model variants, with guardrail metrics as non-negotiable constraints.
- **Feature flags and kill switches** per capability, tool, model and tenant.

---

## 6. Core capability: Insights

Natural-language analytics over the reporting warehouse. **This is the answer to "can it pick the store and filters and write SQL": yes.** Accuracy comes from the semantic catalogue, value grounding and the verifier. The model is not trusted to write SQL freely.

**Example questions:**
- "Net outflows by entity and currency last week, value-date basis."
- "Which accounts had statements missing for 2+ days this month?"
- "Unmatched statement entries older than 5 days by bank."
- "How did EUR cash move week over week, and what drove it?"

**Out of scope:** writes, real-time operational data, and FX conversion without an approved rate source.

### 6.1 Pipeline

```text
Question
 → Understand   (Planner → MetricSpec; glossary resolution; ambiguities → elicitation)
 → Identify     (store/column retrieval + golden routing table → SchemaBinding)
 → Ground       (filter values matched against real data; relative dates resolved in code;
                 mandatory filters: current-version rows; user scope applied by the Query API)
 → Plan         (LLM fills a structured QueryPlan over catalogue identifiers)
 → Render       (code renders SQL from QueryPlan + catalogue join definitions)
 → Verify       (deterministic checks, then an LLM semantic check that can only reject)
 → Execute      (Query API → Postgres, as the user; read-only, timeout, row cap, as-of time)
 → Analyse      (Python computes totals, deltas, drivers, outliers)
 → Explain      (LLM narrates from the computed table; numbers post-checked)
 → Answer       (answer contract + generative UI spec)
```

### 6.2 Semantic catalogue

One YAML file per store, versioned in the registry, retrieved through hybrid search and loaded into memory for validation:

```yaml
store: cash_transaction
definition: "One row per cash transaction version."
owner: { business: treasury-ops, steward: data-platform }
grain: [transaction_id, version]
current_row_filter: "is_latest_version = 1"
dates:
  transaction_date: { meaning: "Recognition date", tz: "entity local" }
  value_date:       { meaning: "Funds have value; use for cash positioning" }
  snapshot_date:    { meaning: "EOD snapshot; freshness only" }
money:
  amount:    { currency_column: currency, sign: "always positive; use direction" }
  direction: { values: [IN, OUT], perspective: entity }
relationships:
  - { to: cash_movement, on: [transaction_id], cardinality: one_to_many }
dimensions: [entity, business_unit, counterparty, currency, transaction_type, state]
state_values: { SETTLED: "Application-settled; not bank-confirmed unless reconciled" }
sensitivity: { counterparty_account: restricted, remittance_info: restricted }
synonyms: { payments: "transaction_type in (...)", outflows: "direction = OUT" }
known_issues: ["Rows cancelled after snapshot remain until next EOD."]
example_questions: ["Outflows by entity last week", "..."]
```

It comes with:
- a **business glossary** ("cash position", "settled", "exposure", "booked");
- a **golden query bank** of verified question–plan–SQL triples, used for few-shot retrieval and routing;
- a **routing table** that maps common metrics to their preferred store.

**Catalogue operations:**
- owners and review workflow;
- a CI schema-drift check against warehouse metadata;
- a coverage dashboard;
- synonym mining from query logs (proposals reviewed by stewards);
- an export adapter to a standard semantic-layer format (dbt MetricFlow or Cube) [DECIDE].

### 6.3 Typed contracts

```python
class MetricSpec(BaseModel):
    measures: list[str]
    dimensions: list[str]
    filters: list[str]                      # natural phrases, grounded later
    time_window: tuple[date, date]          # end exclusive, resolved in code
    date_semantics: Literal["value_date", "transaction_date", "booking_date", "settlement_date"]
    currency_policy: Literal["group_by_currency", "convert_to", "single_currency"]
    convert_to: str | None = None
    expected_grain: str
    ambiguities: list[str] = []

class GroundedFilter(BaseModel):
    column: str
    op: Literal["=", "!=", "in", "not in", ">=", "<", "between", "is null"]
    values: list[str]
    match_confidence: float                 # < 0.9 → confirm via elicitation
    source_phrase: str

class QueryPlan(BaseModel):
    store: str
    joins: list[str]                        # named catalogue relationships only
    select: list[str]
    group_by: list[str]
    filters: list[GroundedFilter]
    order_by: list[str] = []
    limit: int
```

### 6.4 Store identification and filter grounding

**Store choice.** Hybrid retrieval over the store and column catalogue entries (top-k 8), re-ranked by:
- coverage of the requested measures and dimensions;
- grain compatibility;
- golden routing-table hits;
- freshness suitability.

If two stores would answer differently, the answer states the difference or asks.

**Value grounding.**
- Filter phrases are matched against distinct values with fuzzy and embedding matching. Confidence below 0.9 triggers confirmation. No match means the answer says so; a filter that silently matches nothing is never emitted.
- Relative time ("last quarter", "YTD") is resolved in code using the entity's calendar and time zone, as half-open intervals.
- Mandatory filters are injected: the current-version filter and classification filters. The user's data scope is applied by the Query API (D6). If the underlying system cannot enforce scope, the documented fallback is to inject scope predicates from the permissions endpoint (ARCHITECTURE.md §5.2).
- Grounding lookups (`sample_values`) also run through the Query API as the user, so the copilot never shows values the user cannot see.

**Generation.** Constrained `QueryPlan` first: code renders the SQL, which eliminates invented identifiers and ad-hoc joins. Free-form SQL is a fallback for complex shapes (windows, CTEs) and gets the same verification.

### 6.5 Verifier (deterministic first)

1. Parse in the Postgres dialect (`sqlglot`). Exactly one SELECT on one database is allowed.
2. Qualify all identifiers and reject any that is not in the catalogue.
3. Reject sensitive columns unless purpose and scope allow them.
4. Require the current-version filter unless history was requested.
5. Fan-out check: use catalogue cardinality, then confirm empirically with COUNT versus COUNT DISTINCT on the grain key before and after each join.
6. Currency rule: money aggregates must group by currency or filter to one currency, unless an approved conversion is used.
7. Date rule: the filtered date column must match `date_semantics`; intervals must be half-open.
8. Cost guard: Postgres plan via the Query API's `/v1/explain`; no unfiltered scans of large stores; LIMIT and timeout enforced.
9. Hygiene: parameterized literals, a function allowlist, no cross joins.
10. LLM semantic check (high effort). It can only reject.
11. Result sanity: non-negative counts, complete date coverage, and optional reconciliation against trusted totals.

The Query API repeats the critical checks server-side (single SELECT, read-only transaction, timeout, row cap), so the copilot's verifier is never the only line of defence.

Each rejection returns a structured reason to the planner. After 3 attempts the answer is "could not produce a verified answer", with the reasons shown.

### 6.6 Analysis, explanation and answer contract

- **Analysis in Python:** totals, deltas, period-over-period, variance decomposition by dimension with mix effects, top-N, robust outliers (MAD), and data-quality flags.
- **Narrative:** the LLM writes from the computed table only. A post-check rejects any number that is not in the results. Speculation is labelled `INFERENCE`.
- **Answer contract:**
  - the answer and its table or chart spec;
  - the assumptions made;
  - store, grain, date field and currency handling;
  - snapshot time and freshness lag;
  - row count and warnings;
  - SQL and lineage on demand;
  - suggested follow-ups;
  - feedback controls with reasons (wrong number, wrong filter, unclear).
- **Failure behaviours:**
  - unverifiable: explain why;
  - empty result: diagnose which filter excluded the rows (relaxed counts);
  - too broad: ask to narrow;
  - restricted data: say that scope limits the answer, without revealing what is hidden.
- **Multi-turn:** follow-ups apply typed deltas to the previous `MetricSpec` and re-verify. A topic switch resets state.

### 6.7 Evaluation targets

Gold set of 150–200 questions authored independently of the system, pinned to a synthetic-world version. Each question has reference SQL, a reference result, grain, date field and currency policy. It is split into single-store, joins, time comparisons, ambiguous wording, traps (cross-currency, fan-out, wrong date field, restricted scope, stale snapshot) and unanswerable questions.

| Metric | Target [TWEAK] |
|---|---|
| Execution accuracy | ≥ 85% overall; ≥ 95% single-store |
| Trap safety (refuse or answer correctly) | 100% |
| Silent-wrong rate | ≤ 2% |
| Verifier catch rate on bad drafts | ≥ 90% |
| Store selection recall@3 | ≥ 98% |
| Filter grounding F1 | ≥ 0.95 |
| Clarification precision (human-judged) | ≥ 80% |
| Latency p50 / p95 | < 20 s / < 60 s |

**Public benchmark track:** also report accuracy on BIRD and/or Spider 2.0 [VERIFY current versions], so results are comparable with published systems.

### 6.8 C2 — Dashboard Composer (Wave 2)

Turns requests into a validated `DashboardSpec` built on C1.

**Agents:** intent → `SpecDelta`, field mapping (grounded values), layout, critic.

**Every panel compiles through C1's verifier.**

**Data model:** `Panel` (store, measure, primary/secondary grouping, filters, chart type, `currency_split` forced for money, date field, time window, top-N) and `DashboardSpec` (versioned, assumptions, owner).

**Edits and storage:**
- Edits are typed deltas (`add_panel`, `update_panel`, `remove_panel`, `set_global_filter`) with undo.
- Specs are stored as drafts. Publishing goes through PL3.

**Layout heuristics (deterministic first):**
- time dimension → line;
- one categorical with ≤ 12 members → bar;
- two categoricals → stacked bar or heatmap;
- single number → KPI tile;
- more than 12 members → top-N plus "other".

**Critic checks:**
- money without a currency split;
- mixed date fields;
- stale or empty data;
- filter values not found;
- more than 8 panels;
- duplicates.

**Targets:** field-level spec accuracy ≥ 85%; first-pass acceptance ≥ 60%; median ≤ 3 turns.

---

## 7. Deep capability: Reconciliation Resolver

Proposes matches between internal items and bank statement entries, including 1:N, N:1 and N:M groupings. **Primary metric: false-match rate**, because a false match hides a real discrepancy. It never finalizes a match.

### 7.1 Pipeline

```text
Unmatched statement entries + unmatched internal items
 → Remittance extraction (regex first; LLM fills gaps; no tools; tainted input)
 → Candidate generation (deterministic blocking)
 → Combinatorial matcher (OR-Tools CP-SAT)
 → Scorer (calibrated probability)
 → Explainer (narrates computed residuals)
 → Adjudicator (SUGGEST / REVIEW / LEAVE OPEN)
 → Draft MatchProposal → PL3 review UI → decision → labels
```

### 7.2 Design

**Extraction:**
- Output is a `RemittanceFields` model (invoice refs, end-to-end id, payment refs, counterparty name, validated IBAN only, purpose hint, fee mention, per-field confidence).
- Every extracted reference must be a normalized substring of the source text, otherwise it is discarded. This blocks hallucinated references and injected ones.

**Blocking [TWEAK]:**
- same account and same currency;
- value date within ±3 business days;
- amount within the fee tolerance (1:1), or ≤ the entry plus tolerance (grouping);
- consistent direction.

Strong keys short-circuit to a high-prior candidate.

**CP-SAT (N:1):**
- binary x_i per candidate;
- |Σ x_i·amount_i − entry| ≤ tolerance;
- 1 ≤ Σ x_i ≤ 8;
- same counterparty unless a netting agreement exists;
- maximize Σ affinity − λ·count;
- 2 s limit per entry; amounts in integer minor units.

Variants:
- 1:N mirrors the N:1 formulation;
- N:M solves within a blocked cluster at small sizes;
- each cluster returns its top 3 solutions;
- a global constraint stops any item being used twice in a batch.

**Scoring:**
- **Features:** reference overlap, residual (absolute and bps), date distance, name similarity, group size, historical netting pattern, known-fee residual, extraction confidence.
- **Model:** a gradient-boosted classifier trained on approval decisions with isotonic calibration. It runs in shadow mode with a hand-weighted score until enough labels exist.

**Adjudicator [TWEAK]:**

| Calibrated score and conditions | Action |
|---|---|
| ≥ 0.995, group ≤ 3, and residual 0 or a known fee | SUGGEST (pre-ticked in review) |
| 0.90 – 0.995 | REVIEW with alternatives |
| < 0.90 | LEAVE OPEN |

A solver timeout degrades to LEAVE OPEN, never to a weaker match.

**Review UI:**
- side-by-side view: agreeing fields in green, conflicts in red;
- residual explanation and alternatives;
- accept, reject or edit; every decision becomes a training label.

### 7.3 Evaluation

**Data:**
- synthetic-world reconciliation faults with labels;
- look-alike decoys;
- a hand-labelled hard set of 200 N:M cases.

| Metric | Target [TWEAK] |
|---|---|
| Precision at SUGGEST | ≥ 99.5% |
| False-match rate (accepted, later reversed) | ≤ 0.5% |
| Correct grouping in top 3 (resolvable breaks) | ≥ 85% |
| N:M hard-set resolution | ≥ 60% |
| Extraction field F1 | ≥ 0.95 |
| Calibration (ECE) | ≤ 0.02 |
| Injection resistance (adversarial remittance) | 0 drafted matches to injected targets |
| Backlog and break age (simulated operations) | −40–60% items, −50% age vs rules-only baseline |

---

## 8. Deep capability: Payment Integrity Sentinel

Detects fraud and control-evasion sequences in payments, and writes evidence-cited case files. **Alerts only. It never blocks, releases or changes a payment.**

### 8.1 Pipeline

```text
Events: SSI / account / counterparty / entitlement changes, payment create/approve/release,
        statement entries, hidden-content flags (PL1), confirmation SSI mismatches (B1)
 → Temporal graph (entities, counterparties, SSIs, accounts, users, payments, changes)
 → Detectors D1–D10 (deterministic + ML)
 → Score fusion (calibrated) → alert above threshold
 → Investigator agent (read-only) → CaseFile
 → Policy agent → controls and required human checks
 → Analyst review (PL3) → disposition → labels
```

### 8.2 Detectors [TWEAK thresholds]

| ID | Logic | Example threshold |
|---|---|---|
| D1 change-then-pay | Payment to an SSI or account changed recently; weighted up if the same user changed it and created the payment | 10 days |
| D2 first-time beneficiary | No prior payment to the account | 365-day lookback |
| D3 structuring | ≥ 2 payments to one beneficiary that each fall under the approval limit but together exceed it | 3 business days; each ≥ 70% of limit |
| D4 maker-checker anomaly | Same person, mutual-approval pairs, approval within seconds | < 60 s |
| D5 unusual approver or time | Outside the usual approver set or the entity's hours | z > 3 |
| D6 velocity | Against a rolling baseline | > 5× 90-day median |
| D7 shared account | One IBAN linked to unrelated counterparties | ≥ 2 |
| D8 confirmation SSI mismatch | From B1 | any |
| D9 hidden content | From PL1 stripping logs | any |
| D10 anomaly score | Platform anomaly score | top 1% |

**Score fusion:**
- Start with transparent weights.
- Once there are ≥ 200 dispositions, train a calibrated logistic or GBM model.
- Every alert shows which detectors fired.

**Phase-2 graph features:** degree changes, new edges and community shifts.

### 8.3 Case file

```python
class CaseFile(BaseModel):
    case_id: str
    risk_score: float
    patterns: list[str]                              # detectors fired
    timeline: list[tuple[datetime, str, Evidence]]   # every event cited
    summary: str                                     # 3–5 sentences, numbers post-checked
    affected_payments: list[str]
    exposure: list[Money]                            # per currency, never summed across
    recommended_checks: list[str]                    # e.g. call back using master-data number
    controls: list[str]
    open_questions: list[str]
```

The investigator cites evidence for every event and never recommends releasing a payment.

### 8.4 Evaluation

| Metric | Target [TWEAK] |
|---|---|
| Seeded-scenario detection | ≥ 95%, with median time-to-alert reported |
| Precision@k (weekly top alerts) | ≥ 30–50% |
| False positives vs anomaly-only baseline at equal recall | −30–50% |
| Case-file factual accuracy | ≥ 98% of timeline events and claims |
| Triage time per alert (moderated user study) | −60–80% vs raw-score alerts |

---

## 9. Backlog capabilities

These are designed but not scheduled. Each follows the same template when promoted: gold set and baseline first, threat-model update, eval gates, PL3 approvals.

| ID | Capability | Key design ideas | Headline metrics |
|---|---|---|---|
| B1 | Trade Confirmation Intelligence | MT3xx deterministic parser; PDF/email layout parsing + extraction with per-field confidence and click-to-verify spans; product disambiguation (spot vs swap); field tolerances; cross-checks (amount × rate, business days); counterparty templates after 20+ samples; factual dispute drafts; SSI-mismatch signal to C4 | Field F1 ≥ 0.97; touchless 60–80%; mismatch recall ≥ 99% |
| B2 | Bank Fee Audit | Human-verified fee rules extracted once per contract; deterministic expected-fee engine (tiers, minimums, caps, effective dates, Decimal); volume reconciliation against warehouse counts; variance categories; dispute drafts; recovery tracking of credits actually received | Engine 100% on reference lines; variance precision ≥ 90%; recovered as % of audited spend |
| B3 | Bank Onboarding Factory | Bank guide → `BankProfile` with page citations → `MappingSpec` built only from a named transform library → XSD-valid synthetic messages → generated test suites → execute → diff analyzer (mapping bug / test bug / spec ambiguity) → repair loop with cap → approval package | Field agreement ≥ 90% on replay; 100% element test coverage |
| B4 | Configuration Impact Analyzer | Config graph (entities, accounts, SSIs, rules, mappings, message types, stores, reports, tests, incidents); parameterized traversal templates; enrichment with volumes; incident GraphRAG; risk score; regression test selection; "dependency unknown" never treated as "none" | Impact recall ≥ 90%; tests cut 50–70% at ≥ 85% defect catch |
| B5 | Audit Evidence Agent | `ControlTest` → policy RAG (version-effective) → verified population queries via C1 → seeded sampling → lineage → attribute predicates in code → cited `EvidencePack` + reproducibility bundle; SoD matrix from entitlements | 100% citation coverage; 100% reproducible; ≥ 95% exception agreement |
| B6 | Regulatory Reporting Assembler | Regulation field graph → validation rules → source lineage; deterministic validators (LEI, UTI, code lists); root-cause clustering; abstain when lineage is unknown; drafts only | Root-cause ≥ 85%; abstention 100% |

---

## 10. Safety: guardrails, PII, security

### 10.1 Intent and routing

**Taxonomy [TWEAK]:**
- capability intents: `data_insight` (`lookup`, `aggregate`, `trend`, `comparison`, `variance_explain`, `anomaly_check`, `freshness_status`), `doc_qa`, `dashboard_request`, `recon_assist`, `fraud_case`;
- conversational: `smalltalk`, `meta`;
- refusal classes: `out_of_scope`, `unsafe_or_policy_violation`.

**Layered routing:**
1. Deterministic pre-router: rules, UI context, slash commands.
2. Small calibrated classifier.
3. LLM router with an enum schema, only when confidence is low.
4. Clarification via elicitation. Never guess for DRAFT intents.

**Training data:** synthetic paraphrases, typos, abbreviations and multilingual variants, plus labelled conversation logs from the demo tenant. A held-out set is authored separately.

**Targets:**
- macro-F1 ≥ 0.92;
- unsafe/out-of-scope recall ≥ 0.99;
- ECE < 0.05;
- clarification rate ≤ 25%.

### 10.2 Guardrail layers

| Layer | Controls |
|---|---|
| Input | Unicode normalization, hidden-character stripping, length limits, language ID, rate limits, injection and jailbreak classifiers, PII detection and redaction |
| Retrieval | Source allowlist, ACL filters inside queries, tenant-isolated indexes, tainted-content wrapping |
| Planning | Plan-then-execute, declared tool plans, per-agent allowlists, run caps |
| Tool | Schema validation, SQL verifier, row caps, small-cell suppression (k < 5), Query API executing read-only as the user |
| Output | Number grounding, citation checks, PII and secret scan, scope re-check, off-topic and toxicity filter, safe rendering (no active content, no auto-loaded external links or images) |
| Conversation | Topic policy, refusal style guide, human escalation, abuse detection |
| Operations | Kill switches, feature flags, canary rollout |

Implementation (D7): custom deterministic rails plus small classifiers that run inside the service containers (so they also run on a laptop). Managed guardrails (Bedrock Guardrails on AWS, Content Safety / Prompt Shields on Azure) are an optional extra layer, never a replacement. Every rail has its own test set with recall, false-block rate and latency. A rail without an evaluation is not counted as a control.

### 10.3 PII and data protection

- **Classification:**
  - Every column and document carries a class: public, internal, confidential or restricted.
  - The gateway and the LLM gateway enforce handling per class.
  - Models are hosted (D3). Restricted values are redacted or tokenized before model calls whenever the task allows, and calls go through private endpoints in the cloud account's region (Bedrock / AI Foundry).
- **Detection:**
  - Recognizers combine rules, checksums (IBAN mod-97, Luhn, BIC format) and NER, with custom entities (account numbers, SSI ids, counterparty names).
  - Recall target ≥ 0.98 for payment identifiers, with false positives monitored.
- **Handling:**
  - Redact or tokenize before model calls when the task allows.
  - A reversible vault exists only for authorized display.
  - No raw prompts in trace attributes.
  - An output scan blocks leaks.
- **Lifecycle:**
  - Retention schedules per artefact (conversations, traces, drafts, feedback).
  - Erasure and access requests; legal hold.
  - Encryption at rest with customer-managed keys; TLS in transit.
- **Vendors:** region pinning, zero-retention / no-training terms with hosted model providers, and a documented subprocessor list.
- **Tests:** canary strings planted in the synthetic world, PII-leakage metrics in CI, and cross-tenant leakage tests.

### 10.4 Memory governance

- **Session memory:** conversation state per user, per tenant, with a TTL.
- **Long-term memory** (preferences, saved definitions, frequent filters):
  - opt-in and visible;
  - editable and deletable by the user from a "what I remember" panel;
  - never stores restricted data values;
  - tenant-isolated;
  - versioned and attributable in traces.
- **Memory is untrusted at read time.** It is treated as hints and re-checked against current entitlements and the catalogue.
- **Organizational knowledge** (synonyms, golden queries) is learned only through steward review, never written automatically from conversations.

### 10.5 Security

Full design is in ARCHITECTURE.md §5. Summary:
- OIDC SSO with MFA and step-up authentication.
- Short-lived, audience-bound tokens at every hop via token exchange. MCP servers act as OAuth resource servers, and tokens are never passed through.
- Workload identity with mTLS.
- Authorization split (D6):
  - **Data scope** (rows, columns, entities, accounts) is owned by the underlying system and enforced by the Query API, which executes as the user.
  - **Capability and tool access** are owned by the copilot: role claims plus the versioned YAML tool policy in PL1. No external policy engine is needed for now.
  - Result caches are keyed per user, because the copilot cannot see which users share a scope.
- Tenant id in every key and filter.
- A threat model maintained against the OWASP LLM Top 10, MITRE ATLAS and STRIDE.
- Supply-chain controls (§13).
- Pen testing and LLM red-teaming before each major release.

---

## 11. Evaluation

### 11.1 Principles

- Gold sets are authored independently of the system and pinned to a synthetic-world version.
- Held-out sets are never used for prompt tuning.
- Results are reported with denominators and bootstrap confidence intervals.
- Numeric and SQL correctness are checked programmatically, never by an LLM judge.
- Every production failure becomes a regression case.

### 11.2 Eval layers

| Layer | Metrics | Method |
|---|---|---|
| Intent / routing | Precision, recall, F1 (macro / micro / per class), confusion matrix, ECE, unsafe recall | Labelled and held-out sets |
| Spec extraction | Field-level precision / recall / F1 for measures, dimensions, filters, windows, date semantics | Against reference `MetricSpec` |
| Store / column retrieval | Recall@k, MRR, nDCG, routing-table hit rate | Gold mappings |
| Filter grounding | Value-match precision / recall / F1; silent-empty-filter rate | Gold filter values |
| SQL | Execution accuracy, AST-normalized structure match, valid-SQL rate, verifier catch rate, silent-wrong rate, trap safety, rows scanned | Frozen world snapshot |
| Narrative | Numeric faithfulness (programmatic), caveat completeness, usefulness (rubric) | Post-check + calibrated LLM judge + human sample |
| Document RAG | Contextual precision / recall / relevancy, faithfulness, answer relevancy, citation correctness | DeepEval + own recall@k |
| Agents / tools | Tool correctness (tool and args), task completion, step efficiency, loop rate | DeepEval agent metrics + trace assertions |
| Matching / detection (C3, C4) | Precision, recall, F1, AUC-PR, ROC-AUC, calibration curves, cost-weighted thresholds | Labelled synthetic faults + hard sets |
| Safety | Injection ASR, jailbreak success, PII leakage, scope leakage, toxicity, bias, refusal correctness, over-refusal rate | Red-team suite + DeepEval safety metrics + canaries |
| Performance / cost | Latency per stage (p50/p95/p99), tokens and cost per question, cache hit rate, error rate | Load tests + traces |
| Online | Feedback rate and reasons, correction rate, escalations, repeated questions, drift | PL2 dashboards |

### 11.3 DeepEval and LLM judges

DeepEval is the test runner layer through its pytest integration [VERIFY current API and metric names]:
- `LLMTestCase` per gold item;
- RAG metrics (AnswerRelevancy, Faithfulness, Contextual Precision / Recall / Relevancy);
- agent metrics (ToolCorrectness, TaskCompletion);
- safety metrics (PIILeakage, Bias, Toxicity, Hallucination);
- **GEval** for custom rubrics (e.g. "states date field, currency handling and snapshot time").

**Judge discipline:**
- The judge model is pinned and differs from the generator where possible.
- Each judge metric is calibrated on about 100 human-labelled items, and Cohen's kappa is reported before its threshold is trusted.
- Thresholds are versioned in the registry.
- Judges run several times and the median is used; judge cost is tracked.

### 11.4 Eval flywheel

```text
Production / demo traffic
 → negative feedback, low confidence, guardrail hits, verifier rejections
 → triage queue (sampled, redacted)
 → human label + root-cause tag (catalogue gap, grounding, planning, verifier, narrative, safety)
 → fix (catalogue PR, prompt change, new rule) + new regression case
 → eval set version bump → CI gate → release
```

Root-cause tags are aggregated per release and steer the next work. Catalogue gaps usually dominate, which is why catalogue operations are a product feature.

### 11.5 Release gates [TWEAK]

A release is blocked if:
- execution accuracy falls below its floor or drops more than 2 points;
- any silent-wrong answer appears on the trap set;
- any scope, tenant or PII leak is found;
- injection ASR is above 0;
- intent macro-F1 falls below its floor;
- p95 latency or cost per question exceeds budget;
- a calibrated judge metric regresses beyond tolerance.

Canary releases roll back automatically on SLO or guardrail-metric regression.

### 11.6 Published release report

Each release publishes:
- eval results with confidence intervals;
- public benchmark scores;
- red-team results;
- load and soak results (k6/Locust);
- chaos experiment outcomes (provider outage, slow warehouse, gateway down → fail closed);
- a cost-versus-quality comparison of model routes, used to justify the routing table.

---

## 12. Product engineering

### 12.1 User experience and generative UI

- **Typed UI specs, not markdown.** The orchestrator returns typed components (answer text, table, chart spec, KPI tile, assumptions list, clarification chips, draft card, approval status). The widget renders them with a vetted component library.
- **Streaming.** Tokens and stage progress events ("identified store", "grounding filters", "verifying") stream over SSE.
- **Trust surfaces.** Assumptions, freshness and definitions are always visible; SQL and lineage are one click away; DRAFT and "needs approval" states are clearly labelled.
- **Governed export.** CSV/XLSX downloads apply the same scope as the answer and are watermarked.
- **Accessibility and localization.** WCAG 2.2 AA; languages [DECIDE].
- **Embedding.** A web component or script tag. Host context (current screen, selected entity) is passed as untrusted hints.
- **Admin console.** Catalogue editor with review, policy viewer, registry and experiments, eval dashboards, triage queue, tenant management.

### 12.2 Multi-tenancy

- **Tenancy model:** pooled with strict tenant keys by default; a silo option for regulated tenants [DECIDE].
- **Tenant onboarding:** a provisioning workflow creates the namespace, per-tenant KMS key, index namespaces, entitlements, catalogue overlay and budget.
- **Tenant id everywhere:** tokens, cache keys, vector namespaces, graph labels, traces, ledgers and drafts.
- **Per-tenant settings:** quotas, model routes, data-classification rules and retention.
- **CI leakage suite:** cross-tenant retrieval, caching, memory and trace-viewer tests.

### 12.3 Developer experience

- **`make up`** starts the full local stack with a seeded synthetic world. **`make demo`** runs a scripted demo conversation.
- **Preview environments per pull request.** The eval suite runs against them and posts a summary to the PR.
- **Contract tests** for every MCP tool schema; typed client SDKs generated from the schemas.
- **Pre-commit checks:** lint, type check, secret scan, catalogue schema validation.
- **Docs site:** architecture, ADRs, runbooks, API reference, eval reports.

### 12.4 Reliability and cost

The compute model, SLOs, DR, caching and FinOps are in ARCHITECTURE.md §§6, 12 and 13.

Product-level commitments:
- **Usage profile (D10):** on-demand analytics at low volume, so the copilot does not need high-throughput infrastructure.
- **Availability:** the copilot is available whenever the host application is (default 99.9% monthly). Interactive services run as always-warm serverless containers (ECS Fargate / Azure Container Apps, at least 2 instances in production). Functions and scheduled jobs handle event-driven and batch work.
- **Failure isolation:** a copilot outage never affects the host application;
- cost attribution per question, user, tenant and capability;
- budgets enforced in the gateways;
- a published cost-versus-quality comparison per release.

---

## 13. Governance and compliance evidence

- **Model cards per agent:** intended use, out-of-scope uses, models and versions, data classes handled, eval results, known limitations, owners.
- **System card for the product:** capabilities, human oversight points, safety measures, evaluation summary.
- **Control mapping:** SOC 2 and ISO 27001 controls, NIST AI RMF functions (Govern, Map, Measure, Manage) and the EU AI Act transparency points, each mapped to evidence (tests, configs, logs) [VERIFY current versions].
- **Change management:** prompts, policies, catalogue and model routes are code. Every change is reviewed, eval-gated and recorded in the registry.
- **Supply chain:**
  - pinned dependencies;
  - an SBOM per image;
  - vulnerability scanning;
  - signed images;
  - SLSA provenance;
  - admission policies;
  - internal MCP servers only, with pinned manifests.
- **Threat model:** maintained per release. Abuse cases become red-team tests.
- **Audit trail:** hash-chained tool ledger, decision store and WORM log archive.

---

## 14. Delivery stages

Stages have exit criteria, not dates. Detailed planning happens in the next phase.

| Stage | Contents | Exit criteria |
|---|---|---|
| S0 Foundations | ADRs for the open decisions (§16); threat model v1; data-classification map; repository scaffold; CI with lint, type, security scans | ADRs accepted; CI green on an empty skeleton |
| S1 Synthetic world | Generator v1 with manifest, faults, labels, document corpus, injection corpus; property tests | Deterministic world v1 loads into the Postgres analytics databases (queried through the reference Query API), mock APIs and document store |
| S2 Eval harness | Intent set, Insights gold set v1 (60) with traps, DeepEval + programmatic SQL runner, release-gate config, report generator | Baseline report produced for a naive text-to-SQL agent |
| S3 Platform | BFF + SSO + token exchange; orchestrator; four MCP server skeletons; PL1 minimal; PL2 tracing; guardrails v1; PII; registry; kill switches; local `make up` | Authenticated, traced, redacted, audited doc Q&A; red-team v1 passes |
| S4 Insights | Catalogue for core stores, glossary, golden queries, grounding, QueryPlan rendering, verifier, analysis, answer contract, generative UI, elicitation, gold set to 150+, public benchmark track | Insights targets met (§6.7); zero leaks; release report v1 |
| S5 Hardening | Approval service (PL3), experimentation (PL4 promotion flow), multi-tenancy, load / chaos / soak, pen test and red-team, AWS deployment via Terraform (ECS Fargate), Azure deployment (Container Apps), model and system cards | Security sign-off; SLOs met under load; AWS deployment live; Azure deployment passes the same smoke and eval suite |
| S6 Reconciliation | C3 end to end with the review UI and calibration | C3 targets met (§7.3) |
| S7 Sentinel | C4 end to end with case files | C4 targets met (§8.4) |
| S8+ | C2, then backlog capabilities by value | Per-capability targets |

---

## 15. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Synthetic world feels unrealistic | Showcase loses credibility | Realism controls, faults modelled on real patterns, public benchmark track alongside |
| Catalogue gaps | Wrong answers at scale | Owners, review, drift CI, coverage metrics, flywheel root-cause tags |
| Silent-wrong aggregation | Loss of trust | Verifier, trap set, grounding post-checks, visible caveats |
| Cross-scope or cross-tenant leak | Critical failure | Scope enforced by the underlying system via the Query API, per-user caches, ACL-in-query for documents, tenant keys, CI leakage suite, pen test |
| Underlying system cannot enforce row scope for the Query API | Copilot must take on data authorization | Documented fallback: scope predicates from the permissions endpoint (ARCHITECTURE.md §5.2) |
| Prompt injection via data | Unsafe drafts or disclosure | Taint tracking, plan-then-execute, no tools on extractors, substring rule |
| Scope creep across 14 capabilities | Thin product | Depth-first scope (§2); backlog stays designed, not built |
| Model or provider changes | Regressions | Version pinning, shadow + eval gates, fallback routes |
| Cost overrun | Unsustainable demo | Tiering, caching, budgets, per-question cost tracking |
| LLM-judge unreliability | Misleading metrics | Calibration against human labels, programmatic checks for numbers |
| Employer confidentiality | Reputational / legal | Neutral naming, synthetic data only, source notes kept private |

---

## 16. Decisions

| # | Decision | Outcome | Status |
|---|---|---|---|
| D1 | Product name | **Analytics Copilot**, with no organization or brand name attached. The fictional company in the synthetic data keeps the working name *Halcyon Group* until changed. | Decided |
| D2 | Cloud | **AWS primary, Azure second.** GCP deferred. | Decided |
| D3 | Model hosting | **Hosted models** (Bedrock on AWS, AI Foundry on Azure). The full product also runs on a laptop, calling the same hosted APIs. Small classifiers run in-container on CPU. | Decided |
| D4 | Analytics data access | **REST Query API that accepts SQL and runs it on the Postgres analytics databases.** Contract in ARCHITECTURE.md §8.2. Direct database access was considered and rejected; its cautions and fixes are in ARCHITECTURE.md §8.3 and double as the checklist for the reference Query API. | Decided; real API contract to be confirmed |
| D5 | Semantic layer | Own catalogue + MetricFlow/Cube export adapter | Decided |
| D6 | Authorization | **Data authorization stays with the underlying system**, enforced by the Query API executing as the user. The copilot owns only capability and tool policy (YAML, no external engine). | Decided; confirm the underlying system enforces row scope |
| D7 | Guardrails | Custom deterministic rails + in-container classifiers; managed guardrails as an extra layer | Decided |
| D8 | Tenancy | Pooled by default, silo option | Decided |
| D9 | Frontend | Web-component widget; React admin console | Decided |
| D10 | Compute and availability | Low-volume, on-demand usage. **Serverless containers for the interactive path** (ECS Fargate / Container Apps, always warm, at least 2 instances in prod). **Functions and scheduled jobs for events and batch.** Availability matches the host application (default 99.9%). Rationale in ARCHITECTURE.md §6. | Decided; per-question budget and languages still to set |
| D11 | License and public-repository policy | — | **Open** |

Other open items: graph database, durable workflow engine, identity provider for the showcase (ARCHITECTURE.md §15).

---

## 17. Glossary

| Term | Meaning |
|---|---|
| Query API | The underlying system's REST service that runs read-only SQL on the Postgres analytics databases as the requesting user |
| SSI | Standing settlement instruction: where a counterparty wants to be paid |
| Value date | Date funds have value; used for cash positioning |
| Grain | What one row of a store represents |
| Fan-out | Row multiplication from a one-to-many join, which inflates sums |
| Break | Unmatched item between internal records and bank statements |
| N:M match | Several internal items matching several bank entries |
| Structuring | Splitting a payment to stay under approval limits |
| Maker-checker | One person creates, a different person approves |
| SoD | Segregation of duties |
| Taint | Label on data that came from untrusted external text |
| Elicitation | MCP mechanism for a server to request structured input from the user |
| ASR | Attack success rate |
| ECE | Expected calibration error |
| MT940 / camt.053 | Bank statement formats (SWIFT MT and ISO 20022) |
