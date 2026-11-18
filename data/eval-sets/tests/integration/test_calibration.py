"""The harness end to end against the real stack: a ceiling must pass, a floor must fail."""

from collections.abc import AsyncIterator

import httpx
import pytest
from copilot_auth.testing import keycloak_token_for
from copilot_evals.gates import Gates, evaluate_gates
from copilot_evals.loader import load_gold_set
from copilot_evals.models import Capability
from copilot_evals.runner import run_agent
from eval_sets.agents import CarelessAgent, OracleAgent, QueryApiExecutor
from eval_sets.insights_v0 import DATA_FILE
from eval_sets.run import GATES_FILE, _settings
from query_api.app import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
async def executor() -> AsyncIterator[QueryApiExecutor]:
    app = create_app(_settings())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://query-api") as http:
            yield QueryApiExecutor(http, keycloak_token_for)


async def test_the_oracle_clears_every_gate_through_the_real_query_api(
    executor: QueryApiExecutor,
) -> None:
    gold = load_gold_set(DATA_FILE)
    report, results, _ = await run_agent(gold, OracleAgent(executor), seed=1)
    gates = evaluate_gates(report, Gates.from_yaml(GATES_FILE))
    assert all(g.passed for g in gates), [g for g in gates if not g.passed]
    insights = [r for r in results if r.capability is Capability.INSIGHTS]
    assert all(r.passed for r in insights), [r.item_id for r in insights if not r.passed]
    assert report.metrics["valid_sql_rate"].value == 1.0  # every gold query is legal via the API


async def test_the_careless_agent_fails_the_safety_gates(executor: QueryApiExecutor) -> None:
    gold = load_gold_set(DATA_FILE)
    report, _, _ = await run_agent(gold, CarelessAgent(executor), seed=1)
    failed = {g.name for g in evaluate_gates(report, Gates.from_yaml(GATES_FILE)) if not g.passed}
    assert {"execution_accuracy", "trap_safety", "silent_wrong_rate"} <= failed


async def test_the_floor_is_not_trivially_bad(executor: QueryApiExecutor) -> None:
    """Scope still holds for a careless agent: row-level security does not depend on the agent."""
    gold = load_gold_set(DATA_FILE)
    report, _, _ = await run_agent(gold, CarelessAgent(executor), seed=1)
    assert report.scope_leaks == 0
    assert report.metrics["valid_sql_rate"].value == 1.0
