# 0001. Vertical-slice first iteration with fakes behind real ports

- **Status:** Accepted
- **Date:** 2026-11-18
- **Deciders:** Owner
- **Supersedes:** none
- **Spec references:** Analytics Copilot.md §3, §14 (S0–S8), §15; ARCHITECTURE.md §1, §14

## Context

The delivery plan in §14 builds horizontally: S0 foundations, S1 synthetic world, S2 evals, S3 platform, S4 Insights. A reviewer cannot run the product until S4. The first iteration must be a running, authenticated, traced system a reviewer can start with one command, without waiting for the full stage sequence.

Two constraints from the spec stay fixed. The non-negotiables in ARCHITECTURE.md §1 hold in every iteration, and "evaluation before feature" (principle 8) means the gold set and a baseline exist before the agent does.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| A. Follow S0–S8 in order | Matches the written plan; no spec change | Nothing runnable for a long time; the integration risk (auth, gateway, model, UI) surfaces last |
| B. Throwaway proof of concept | Fast | Becomes the codebase anyway; skips guardrails and evals; contradicts the spec |
| C. Vertical slice: one thin path through every layer, external systems faked behind the real interfaces | Runnable early; integration risk found early; real adapters drop in later; keeps the non-negotiables | Some stage content is pulled forward and some deferred; needs discipline to keep fakes honest |

## Decision

Build iteration 1 as a vertical slice (option C): one end-to-end path (chat, policy gateway, MCP analytics tool, verified text-to-SQL, evidenced answer, human-approved draft) with every layer real in shape and external systems replaced by fakes that implement the real contracts.

What is real: authentication and authorization (ADR 0003), the policy gateway, the SQL verifier, the Query API reference service running on Postgres with row-level security, tracing, the eval harness and release gate, and the UI.
What is faked, always behind a port with a contract test: treasury source systems, bank and ERP connectors, the enterprise Query API (the reference service stands in), document sources. Data is the synthetic Halcyon world (D1).
The model is neither faked nor hard-wired. It sits behind a port (ADR 0002).

Fakes are named as fakes (`adapters/fakes/`, `synthetic_world/`) and the README says the system runs offline on synthetic data. Nothing is presented as a live integration it is not.

## Consequences

- Positive: a reviewer can `make up` and use the product; integration problems appear in week one; the S0–S8 content is still the checklist, only reordered.
- Negative / costs: S1–S4 content is partly pulled forward in reduced form (small world, a gold set of about 30, one capability); the full stages are not replaced and still apply to a production path.
- Follow-ups:
  - Slice plan in `docs/stages/V1-vertical-slice.md`.
  - S0 work items W3 and W4 renumber their ADRs, since 0001–0003 are taken here.
  - If accepted, add a note under Analytics Copilot.md §14 that V1 precedes S1–S4, then run `spec-guardian`.

## Verification

- `make up` then the demo script passes on a clean clone.
- `make eval` produces a baseline report and gates on the gold set.
- Leakage and policy tests run in CI.
- Review after V1 exits: list every fake and confirm each has a contract test and a documented real counterpart.

## Open items

- None blocking. The real Query API contract remains **[CONFIRM]** (ARCHITECTURE.md §8.2, D4), unchanged by this ADR.
