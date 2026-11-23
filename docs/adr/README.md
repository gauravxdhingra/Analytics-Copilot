# Architecture Decision Records

One file per decision, numbered `NNNN-kebab-title.md`, from [0000-template.md](0000-template.md). Create them with the `/adr` skill. A decision is never edited away; a new ADR supersedes it.

Backlog of decisions to write up: ARCHITECTURE.md §15. Decision table: Analytics Copilot.md §16.

| # | Title | Status | Decision ref |
|---|---|---|---|
| [0001](0001-vertical-slice-first-iteration.md) | Vertical-slice first iteration with fakes behind real ports | Accepted | Reorders §14 for V1 |
| [0002](0002-model-port-and-gemini-adapter.md) | Provider-neutral model port with a Gemini default adapter | Accepted | D3, ARCH §15 #2 |
| [0003](0003-keycloak-oidc-token-exchange-and-rls.md) | Keycloak OIDC, audience-bound token exchange, and database-enforced scope | Accepted | D6, D8, ARCH §15 #12 |
| [0004](0004-engineering-stack-and-monorepo.md) | Engineering stack and monorepo layout | Proposed | CLAUDE.md Stack |
| [0005](0005-memory-and-caching.md) | Memory and caching: per-user, user-written, and never trusted | Proposed | §10.4, D6, D8 |
| [0006](0006-release-gates-and-budgets.md) | Release-gate thresholds and the per-question budget | Proposed | §11.5, PS:80 |
| [0007](0007-outflows-as-positive-amounts.md) | Outflows are answered as positive amounts through an explicit plan option | Proposed | §6 QueryPlan, §11 |
