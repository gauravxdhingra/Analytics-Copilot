.PHONY: lint typecheck test test-integration fmt up down eval eval-baseline eval-copilot seed

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	# One run per project: several tests/ and conftest.py files share names across projects.
	@for d in packages/* data/synthetic-generator data/eval-sets services/* mcp-servers/*; do uv run mypy $$d || exit 1; done

test:
	uv run pytest packages data services mcp-servers -m 'not integration'

test-integration:
	set -a && . deploy/compose/.env && set +a && uv run pytest packages data services mcp-servers -m integration

ENV = set -a && . deploy/compose/.env && set +a

# Load the synthetic world into the running Postgres, then create roles and row-level security.
seed:
	$(ENV) && uv run halcyon-world load --dsn "postgresql://copilot_owner:$$POSTGRES_OWNER_PASSWORD@localhost:5433/analytics" >/dev/null && uv run provision-query-api

# Release-gate check. Until the copilot agent exists this proves the gates themselves work: a
# ceiling (reference SQL) must clear them and a careless agent must fail them.
eval:
	$(ENV) && uv run run-evals calibrate --out reports

# The naive text-to-SQL baseline. Replays recorded model calls; record them once with a key:
#   GEMINI_API_KEY=... make eval-baseline MODE=record
MODE ?= replay
eval-baseline:
	$(ENV) && uv run run-evals baseline --mode $(MODE) --out reports

# The release gate for the product agent. Replays recorded model calls; record them once with a
# key: GEMINI_API_KEY=... make eval-copilot MODE=record
eval-copilot:
	$(ENV) && uv run run-evals copilot --mode $(MODE) --out reports --enforce

up down:
	@echo "'$@' is not implemented yet (see docs/stages/V1-vertical-slice.md)"; exit 1
