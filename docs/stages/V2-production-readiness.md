# Stage V2: Production readiness

Status: **In progress** (2026-11-19). V2-1 done (ADR 0006 proposed, gates implemented). V2-2 done (pacing, mock-tested). **V2-3 is blocked by owner instruction: no calls to the model provider**, so the agent eval cannot be recorded; everything that does not need a live model proceeds. Builds on [V1](V1-vertical-slice.md). Order and scope were fixed by the owner on 2026-11-19 ("build these first"). Depends on ADRs [0001](../adr/0001-vertical-slice-first-iteration.md) to [0005](../adr/0005-memory-and-caching.md) and the new ADR 0006 (first task).

## Why this stage exists, and what changed since the V1 plan

V1 proved the slice end to end. A close-out review against the spec, the V1 exit criteria and the code found that "done" was overstated and that the plan was out of date. These are the unexpected changes this plan reorganises around:

1. **V1 exit criteria are unmet.** No clean-clone proof; step-up is claimed from token claims, not a real second factor; no agent eval report and no eval in CI; CI has not run on the commits after V1-14 (they are unpushed).
2. **Non-negotiable 8 was breached.** The catalogue, the router and planner prompts and the model provider changed since the last eval, and the agent eval has not been run against them.
3. **Reviewer findings (V1-17).** Four reviewer agents found correctness and security defects; the critical ones are fixed, the rest are open gaps G14 to G25 in the [threat model](../threat-model/v1-vertical-slice.md).
4. **Scope added mid-flight.** Memory and caching ([ADR 0005](../adr/0005-memory-and-caching.md)), a second model provider (Groq), real-model samples ([samples.md](../samples.md)).
5. **A real model exposed behaviour the fake could not:** misread signed columns, structured-output failures, flaky planning on date windows. All were safe, none were measured.
6. **A rate-limited provider** (8,000 tokens per minute on the free tier) makes any naive eval run slow and wasteful, so pacing is a prerequisite, not a nicety.
7. **A gap analysis against the whole spec** found items that had no code at all: filter-value grounding, kill switches and quotas, circuit breakers, feedback capture, analysis tools, retention and backups, model and system cards.

## Goal

Make the slice something that can be released and defended: every claim in the docs is proven by a test or an eval, every release is gated, the failure modes a real model produces are measured and contained, and the operational controls a finance product needs (kill switches, quotas, retention, backups, evidence) exist.

## Exit criteria

From Analytics Copilot.md §14, the rows this stage advances (quoted):

- **S2:** "Baseline report produced for a naive text-to-SQL agent" (met), extended here to a report for the agent and a configured release gate.
- **S3:** "Authenticated, traced, redacted, audited doc Q&A; red-team v1 passes". The doc Q&A part stays out of scope; the rest applies.
- **S4:** "Insights targets met (§6.7); zero leaks; release report v1".
- **S5 (part):** "Security sign-off" (for the controls built here) and "model and system cards".

Measurable targets (§6.7 and §11.5, thresholds are **[TWEAK]** until ADR 0006 is accepted):

| Metric | Target |
|---|---|
| Execution accuracy | at least 85% overall, at least 95% single-store |
| Trap safety | 100% |
| Silent-wrong rate | at most 2%, 0 on the trap set |
| Scope, tenant or PII leaks | 0 |
| Injection attack success rate | 0 |
| Latency p50 / p95 | under 20 s / under 60 s |
| Cost per answered question | within the budget set by ADR 0006 |
| Replay hit rate in CI | 100% |

V1 exit criteria carried forward and closed here: clean-clone run; real MFA step-up on approval; agent eval report plus release gate; CI green on current code.

## Work items

Ordered so that tests, evals and baselines come first (principle 8) and nothing that changes model-facing behaviour lands before the gate that protects it. "Agent" is the project subagent that owns the proof.

### A. Make V1's claims true (first)

| # | Item | Spec | Agent | Proof | Depends on |
|---|---|---|---|---|---|
| V2-1 | **ADR 0006: release-gate thresholds and the per-question budget.** Resolves the [DECIDE] at PS:80; sets floors, the 2-point regression rule, latency and cost budgets as a versioned config | §11.5, PS:80 | eval-engineer | ADR accepted; `data/gates.yaml` cites it; gate unit tests read it | none |
| V2-2 | **Rate-limit-aware model adapters.** Read the provider's remaining-token and reset headers and wait before sending, so a run never burns requests on 429s; honour retry hints (partly done) | §3.3, ARCH §12 | test-engineer | Mock-transport tests for header-driven waiting; a recorded eval run with zero 429 retries | none |
| V2-3 | **[BLOCKED: owner has forbidden provider calls] Record the agent eval and make replay deterministic.** Record `copilot_v0` cassette against the current catalogue, prompts and model; report next to the baseline; replay hit rate 100% | §11, V1 exit | eval-engineer | `make eval-copilot MODE=replay` passes offline; report with confidence intervals committed | V2-1, V2-2 |
| V2-4 | **CI release gate.** A job that starts the database and identity provider, seeds, runs calibration, baseline replay and agent replay with `--enforce`, and uploads the report; a deliberately broken change must fail it | §11.5 | eval-engineer | Same commands green locally; a negative test shows the gate blocks | V2-3 |
| V2-5 | **Clean-clone proof.** A scripted check that clones HEAD, brings the stack up as a separate compose project, runs the browser suite and tears down; wired into the README quickstart claim | V1 exit | infra-reviewer | Script passes from a fresh directory with no `.env` | none |
| V2-6 | **Real step-up.** Keycloak OTP step and ACR-to-level mapping; `acr` and `auth_time` mappers; prove they survive token exchange with a real token; browser test with a generated code. Closes G1 | §5.4, ARCH §5.1 | security-reviewer, test-engineer | Real exchanged token decoded in a test; approval refused without the second factor and allowed with it | none |

### B. Correctness the verifier cannot see (needs the gate from A)

| # | Item | Spec | Agent | Proof | Depends on |
|---|---|---|---|---|---|
| V2-7 | **Filter-value grounding and empty-result diagnosis.** Value dictionaries for dimensions (entities, accounts, counterparties, currencies, statuses); a planner filter value is resolved or refused by code; unknown values become a clarification with close matches; an empty result says which filter excluded the rows (relaxed counts). Metric: silent-empty-filter rate | §6.4, §6.6 | eval-engineer, data-correctness-reviewer | New gold items (misspelled, unknown, near-match values); silent-empty-filter rate reported and gated | V2-4 |
| V2-8 | **Remaining verifier and answer-check gaps (G20).** Number check pairs (number, currency, row); `date_field` flows from plan to verifier and casts are unwrapped; payments follow restated transactions; `SUM(DISTINCT)` and cross-currency `ORDER BY` refused; "as of" per table and in the entity's time zone | §6.5, §6.6 | data-correctness-reviewer | One failing-first test per finding, plus a wrong-date-field trap in the gold set (value dates must differ in the data) | V2-4 |

### C. Operating it (controls a production service needs)

| # | Item | Spec | Agent | Proof | Depends on |
|---|---|---|---|---|---|
| V2-9 | **Kill switches, flags, quotas and circuit breakers.** Switches per capability, tool, model and tenant; per-user and per-tenant quotas and rate limits that persist; circuit breakers and bulkheads per tool server, the Query API and the model provider; everything fails closed | §5.5, §10.2, ARCH §12 | security-reviewer | Tests that each switch refuses with a mapped code and a trace reason; a chaos script (provider down, Query API slow, gateway down) | V2-4 |
| V2-10 | **Feedback capture and the eval flywheel.** Thumbs and reason codes in the UI and API; a triage store (redacted); a tool that turns a triaged failure into a gold-set candidate and a root-cause tag | §11.4, §6.6 | eval-engineer | End-to-end test: feedback in, candidate case out; no raw content stored | V2-4 |

### D. Product depth (changes model-facing behaviour, so each runs through the gate)

| # | Item | Spec | Agent | Proof | Depends on |
|---|---|---|---|---|---|
| V2-11 | **Answer contract completion.** Assumptions list (store, grain, date field, currency handling), freshness lag, suggested follow-ups, "scope limits this answer" without revealing what is hidden, typed UI components | §6.6, §12.1 | test-engineer | Contract tests on every answer; UI tests for each component; caveat-completeness metric | V2-7 |
| V2-12 | **Analysis in Python.** Period-over-period, variance decomposition with mix effects, top-N, robust outliers; the model narrates computed tables only | §6.6 | test-engineer | Property tests (hypothesis) for the arithmetic; gold items for each analysis type | V2-8 |

### E. Evidence and resilience (documents and small code; start in parallel with A)

| # | Item | Spec | Agent | Proof | Depends on |
|---|---|---|---|---|---|
| V2-13 | **Compliance evidence.** Data-classification map; retention schedules, erasure and legal hold; model cards per agent and a system card; control mapping (SOC 2, ISO 27001, NIST AI RMF, EU AI Act) to tests and configs; backup, point-in-time recovery and a tested restore of the ledger and decision store | §10.3, §13, ARCH §12 | spec-guardian | `spec-guardian` clean; a scripted restore test passes | none |
| V2-14 | **Replay, metrics and alerts.** Replay a run against recorded tool results with a fidelity metric; service metrics and SLO burn alerts; the load, soak and chaos plan | §5.3, ARCH §11, §12 | test-engineer | Replay fidelity at least 95% for temperature-0 roles; alert rules fire in a test | V2-3 |
| V2-15 | **Registry and promotion flow.** Versioned artefacts with owner, changelog and eval results; promotion (gate, shadow, canary) and one-click rollback | §5.5 | eval-engineer | Promotion blocked by a failing gate; rollback test | V2-4 |
| V2-16 | **Approval depth.** Changes-requested state, diff view, approver groups per draft type, an `ApprovalSink` adapter | §5.4 | test-engineer | State-machine tests including the database guard; UI tests | V2-6 |
| V2-17 | **Release pipeline and the long tail.** Image build, scan, SBOM and signing in CI; accessibility audit (WCAG 2.2 AA); governed export; red-team suite run with a real model; public benchmark track | §12, §13, §11.6 | infra-reviewer | Pipeline green; audit report; red-team report; benchmark scores | V2-4 |

Also carried, small and independent: security leftovers G14 to G19 (logout token in a URL, the test-only login client, the decision store in the running app, tool error text, cast allow-list, stale roles after refresh).

### Open owner decisions this plan waits on

1. **Data and use case** (see the private data review). Reshaping the world changes the gold set's expected answers. If the owner has not decided by V2-3, record on world v1 and re-record when the new world lands; V2-2 makes that cheap.
2. **Accept ADR 0004 and ADR 0005.**
3. **Push the unpushed commits** so CI runs on current code (the owner pushes; V2-4 and V2-5 prove the same steps locally).

## Risks that apply (Analytics Copilot.md §15)

| Risk | What we do here |
|---|---|
| Silent-wrong aggregation | V2-7 and V2-8, gated by V2-4; traps stay at 100% |
| Cross-scope or cross-tenant leak | Leakage cases are a zero-tolerance gate in V2-4; the memory and cache isolation tests join it |
| Prompt injection via data | Red-team run with a real model in V2-17; abuse cases from the threat model |
| Provider rate limits or drift | V2-2 pacing; replay cassettes pinned in CI; fallback route in V2-9 |
| Cost overrun | Budget in ADR 0006, enforced by V2-4 and per-user quotas in V2-9 |
| Catalogue gaps | V2-7 value dictionaries; V2-10 root-cause tags steer fixes |
| Scope creep | Items D and E are ordered after the gate; anything not listed goes to the backlog |

## Definition of done (per item)

- The proving test or eval fails before the item is built and passes after.
- `make lint typecheck test test-integration eval` is green, and the browser suite for UI changes.
- `/pre-pr` is clean for the change; `spec-guardian` is clean after doc changes.
- New tools or endpoints have a policy entry, a contract test and a trace span.
- The threat model is updated if the attack surface changed.
- No secrets, real data or employer-specific names. Commits are the owner's unless delegated.
- Any prompt, catalogue, model or policy change passes the agent eval first (non-negotiable 8).

## Out of scope for V2

Document questions (C2), reconciliation (C3), the payment integrity sentinel (C4), the backlog capabilities B1 to B6, the admin console and embeddable widget as products, AWS and Azure deployment (the Terraform skeleton may be written and reviewed but is never applied from here), multilingual support, and silo tenancy.
