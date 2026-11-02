# Analytics Copilot

Enterprise treasury assistant: a global chatbot in a web app that answers questions over governed data (governed text-to-SQL), explains how it got each answer, and drafts work for humans to approve. It reaches data and tools only through MCP servers behind a policy gateway. Every step is traced.

**Status:** specification only. No code yet. Next is Stage S0 (see FinAnalytics.md §14).

## Source of truth

| File | Read it for |
|---|---|
| [FinAnalytics.md](FinAnalytics.md) | Product scope, capabilities (PL1–PL4, C1–C4, B1–B6), safety, evals, delivery stages S0–S8, decisions D1–D11 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Containers, request lifecycle, identity, compute, AWS/Azure/laptop deployment, Query API contract (§8.2) |
| `docs/adr/` | Decision records. Template: `docs/adr/0000-template.md` |

Both docs are long (~25k tokens together). **Read only the sections you need** (grep for the section number), do not load them whole. When code and docs disagree, the docs win until an ADR changes them.

Markers in the docs: **[DECIDE]** needs a decision, **[TWEAK]** is a default, **[VERIFY]** check against current vendor docs or spec versions, **[CONFIRM]** confirm with the owner of the system. Never silently resolve one; surface it.

## Non-negotiables

These come from ARCHITECTURE.md §1 and FinAnalytics.md §3. A change that breaks one needs an ADR first.

1. **Read and draft, never execute.** No tool releases payments, changes config or finalizes matches. The top tool tier is `DRAFT`; approval is a separate human service.
2. **Deterministic gates are final.** SQL verification, tool policy and money arithmetic run in code. A model can explain a block, never override it.
3. **The underlying system owns data authorization (D6).** Queries run as the requesting user through the Query API. The copilot adds controls, never replaces them. Caches are keyed per user.
4. **The LLM never computes.** Money, dates, tolerances, fees and matches are computed in Python or SQL.
5. **Untrusted content is data.** Remittance text, PDFs, emails and message payloads are tainted and only reach a model inside `<untrusted source="…">` blocks.
6. **Fail closed.** If the gateway, auth or a guardrail is down, refuse. Never degrade to an unguarded path.
7. **Everything is traced and versioned.** One trace id per run. Prompts, models, catalogue, policies and eval sets are versioned. No raw prompts in span attributes.
8. **Evaluation before feature.** Gold set and baseline first. No prompt, model, catalogue or policy change ships without passing evals.

## Code conventions

- Money is `Decimal` plus an ISO currency, in a `Money` model. Never `float`. Never summed across currencies.
- Date windows are half-open `[start, end)`, resolved in code in the entity's calendar and time zone.
- Every graph node reads and writes Pydantic models. Validate at boundaries.
- Every model and tool call has a timeout and a fallback. Run caps (`MAX_ITERATIONS`, `MAX_TOOL_CALLS`, cost, wall clock) are enforced in code.
- Every claim carries `Evidence` with a `Provenance` label.
- Parameterize all SQL literals. Never interpolate strings into SQL.
- Errors shown to the model or user are mapped codes, not raw database errors.
- Match the surrounding code's naming, comment density and idiom. Comments explain why, not what.

## Stack (proposed defaults, confirm in S0 ADRs)

Python 3.12 managed with `uv`; Pydantic v2; LangGraph; FastAPI; sqlglot; OR-Tools; pytest + hypothesis + DeepEval; `ruff` (lint + format); `mypy --strict`. TypeScript/React for the admin console, web component for the widget. Terraform for AWS (primary) and Azure. Docker Compose for the laptop profile. Repository layout: ARCHITECTURE.md §14.

## Commands

Not created yet. S0 adds a root `Makefile`; then fill in the real commands here.

| Target | Purpose |
|---|---|
| `make up` / `make down` | Local stack (Docker Compose) |
| `make lint` / `make typecheck` / `make test` | Quality gates |
| `make eval` | Eval suite (release gate) |

## How to work here

- **Plan before big changes.** For anything touching more than a couple of files, or any new capability, use plan mode and the `/stage` skill.
- **Test first.** Write the failing test (or eval case) before the implementation. Use the `test-engineer` and `eval-engineer` agents.
- **Decisions get ADRs.** Use `/adr`. Do not bury a decision in a code comment or a commit message.
- **Before opening a PR**, run `/pre-pr`. It runs the reviewer agents and the quality gates.
- **Keep the two docs consistent.** If you change a decision, update both docs and the section/decision references (`spec-guardian` checks this).
- Prefer small, reviewable changes. One concern per commit, Conventional Commits style (`feat:`, `fix:`, `docs:`, `chore:`, `test:`).
- **Do not run `git commit` or `git push` unless asked.** The owner makes commits and pushes themselves. Stage nothing on your own.
- Never put secrets, real customer data or employer-specific names in the repo. The synthetic world uses the fictional *Halcyon Group*. The original source notes stay private and git-ignored.

## Claude Code setup in this repo

| Path | What it is |
|---|---|
| `.claude/settings.json` | Shared permissions and hooks. Personal overrides go in `.claude/settings.local.json` (git-ignored) |
| `.claude/rules/` | Path-scoped rules, loaded only when matching files are touched |
| `.claude/agents/` | Subagents: `spec-guardian`, `security-reviewer`, `data-correctness-reviewer`, `test-engineer`, `eval-engineer`, `infra-reviewer` |
| `.claude/skills/` | Skills: `/adr`, `/stage`, `/new-mcp-tool`, `/catalogue-entry`, `/eval-case`, `/threat-model`, `/pre-pr` |
| `.claude/hooks/` | `protect-files.py` (blocks edits to secrets), `format.py` (auto-format after edits) |
