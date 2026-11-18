# 0004. Engineering stack and monorepo layout

- **Status:** Proposed
- **Date:** 2026-11-18
- **Deciders:** Owner
- **Supersedes:** none
- **Spec references:** Analytics Copilot.md §12.3; ARCHITECTURE.md §14; CLAUDE.md "Stack"

## Context

CLAUDE.md lists proposed defaults and asks for S0 ADRs to confirm them. V1 cannot start code without them. The repository layout is already drawn in ARCHITECTURE.md §14.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| A. Python 3.12 with `uv` workspace, Pydantic v2, FastAPI, LangGraph, sqlglot, `ruff`, `mypy --strict`, pytest + hypothesis; TypeScript/React with Vite for UI | Matches the spec and its typed-boundary principle; one fast toolchain per language | `mypy --strict` costs effort on third-party stubs |
| B. Poetry and pip-tools | Familiar | Slower, no first-class workspaces |
| C. All-TypeScript backend | One language | Weaker fit for sqlglot, OR-Tools and eval tooling |

## Decision

Adopt option A. Python packages live in a single `uv` workspace rooted at the repository, one member per directory under `packages/`, `services/` and `mcp-servers/`. The UI is a separate pnpm or npm workspace under `apps/`. A root `Makefile` is the only interface for lint, typecheck, test, eval, up and down.

## Consequences

- Positive: one command per gate locally and in CI; member packages stay independently testable.
- Negative / costs: strict typing needs stubs or narrow ignores for some libraries, each justified in a comment.
- The MCP server directory is `mcp-servers/`, not the `mcp/` drawn in ARCHITECTURE.md §14 (updated to match). A root directory named `mcp/` makes pytest's path-based module names create a synthetic `mcp` package that shadows the real MCP SDK.
- Follow-ups: CI (V1-2) and CLAUDE.md "Commands" use these targets.

## Verification

`make lint typecheck test` passes locally and in CI from a clean clone.

## Open items

- **[VERIFY]** Current versions of LangGraph, FastAPI, Pydantic and sqlglot at pin time.
- **[TWEAK]** npm vs pnpm for the UI workspace.
