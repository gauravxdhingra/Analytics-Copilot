.PHONY: demo logs e2e web-install web-typecheck web-test web-dev lint typecheck test test-integration fmt up down eval eval-baseline eval-copilot seed env

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	# One run per project: several tests/ and conftest.py files share names across projects.
	@for d in packages/* data/synthetic-generator data/eval-sets services/* mcp-servers/*; do uv run mypy $$d || exit 1; done
	$(MAKE) web-typecheck

test:
	uv run pytest packages data services mcp-servers -m 'not integration'
	$(MAKE) web-test

test-integration:
	set -a && . deploy/compose/.env && set +a && uv run pytest packages data services mcp-servers -m integration

ENV = set -a && . deploy/compose/.env && set +a

# Create deploy/compose/.env with fresh random secrets (never overwrites an existing one).
env:
	uv run python deploy/compose/gen_env.py

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

COMPOSE = docker compose -f deploy/compose/compose.yaml

# The whole stack: Postgres, Keycloak, Jaeger, a bootstrap job, the approval service, the BFF and
# the UI. Creates deploy/compose/.env with random secrets on first run. UI: http://localhost:3000
up:
	uv run python deploy/compose/gen_env.py
	$(COMPOSE) up -d --build --wait
	@echo "UI http://localhost:3000   Keycloak http://localhost:8081   Traces http://localhost:16686"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

# A demo that needs no model key: answers use a canned model. Everything else is real.
demo:
	COPILOT_MODEL=scripted $(MAKE) up
	@echo "Sign in as ana.analyst (HAL-DE), ben.analyst (HAL-UK), maria.manager (both) or amit.approver."
	@echo "Password: DEMO_USER_PASSWORD in deploy/compose/.env"

web-install:
	cd apps/web && npm ci --no-audit --no-fund

web-typecheck:
	cd apps/web && npm run --silent typecheck

web-test:
	cd apps/web && npm test --silent

# UI on :3000, proxying /auth and /api to the BFF on :8000 (start it with `uvicorn bff.main:app`).
web-dev:
	cd apps/web && npm run dev

# Real browser, Keycloak, BFF and Postgres; only the model is scripted. Needs `make seed` and the
# compose stack up. Installs nothing: run `npx playwright install chromium` once beforehand.
e2e:
	set -a && . deploy/compose/.env && set +a && cd apps/web && npx playwright test
