"""The whole pipeline against the real stack, with a scripted model.

This proves the plumbing and the controls (scope, verification, untrusted data, ledger). It is not
an eval: a scripted planner proves nothing about model quality. Quality is measured by the gold
set with a recorded or live model.
"""

import copy
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from copilot_auth import JwksKeyProvider, TokenValidator
from copilot_auth.testing import keycloak_token_for
from halcyon_world import generate_world
from halcyon_world.load import load_world
from orchestrator import AnswerStatus, InsightsOrchestrator, RunCaps
from orchestrator.stack import local_stack
from orchestrator.testing import narrate, plan, route
from policy_gateway import RunContext
from policy_gateway.postgres_ledger import PostgresLedger
from query_api.provision import load_identity, provision

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[4]
ISSUER = f"{os.environ.get('KEYCLOAK_URL', 'http://localhost:8081')}/realms/halcyon"
WORLD = generate_world(seed=7)


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; load deploy/compose/.env")
    return value


@pytest.fixture(scope="module", autouse=True)
def database(owner_dsn: str) -> None:
    load_world(WORLD, owner_dsn)
    provision(
        owner_dsn,
        load_identity(ROOT / "deploy" / "compose" / "identity" / "users.json"),
        login_password=_env("QUERY_DB_PASSWORD"),
    )


async def _ctx(username: str) -> RunContext:
    token = await keycloak_token_for(username, audience="mcp-analytics")
    principal = TokenValidator(
        issuer=ISSUER,
        audience="mcp-analytics",
        keys=JwksKeyProvider(f"{ISSUER}/protocol/openid-connect/certs"),
    ).validate(token)
    return RunContext(
        run_id=f"run-{username}",
        trace_id="trace-e2e",
        principal=principal,
        upstream_tokens={"analytics": token},
    )


@asynccontextmanager
async def _agent(
    script: list[Any], pg_ledger: PostgresLedger, caps: RunCaps | None = None
) -> AsyncIterator[tuple[InsightsOrchestrator, Any]]:
    from copilot_models import FakeClient

    async with local_stack(pg_ledger, env=os.environ) as stack:
        model = FakeClient(script)
        yield (
            InsightsOrchestrator(model, stack.gateway, stack.catalogue, caps, {"policy": "1"}),
            model,
        )


COUNT: dict[str, Any] = {
    "store": "treasury.transactions",
    "select": [{"column": "transactions.transaction_id", "agg": "count", "alias": "n"}],
    "limit": 10,
}


def _booked(*entities: str) -> int:
    return sum(
        1
        for t in WORLD.transactions
        if t.is_current and t.status == "booked" and t.entity_id in entities
    )


async def test_each_user_gets_an_answer_for_their_own_entities(pg_ledger: PostgresLedger) -> None:
    expected = {
        "ana.analyst": _booked("HAL-DE"),
        "ben.analyst": _booked("HAL-UK"),
        "maria.manager": _booked("HAL-DE", "HAL-UK"),
    }
    for user, count in expected.items():
        async with _agent(
            [route(), plan(COUNT), narrate(f"There are {count} booked transactions.")], pg_ledger
        ) as (agent, _):
            answer = await agent.run("How many booked transactions are there?", await _ctx(user))
        assert answer.status is AnswerStatus.ANSWERED, answer.text
        assert answer.table is not None and answer.table.rows == [[count]], user
        assert "as of 2026-06-30" in (answer.text or "").lower()
    assert len(set(expected.values())) == 3


async def test_a_money_total_matches_an_independent_calculation(pg_ledger: PostgresLedger) -> None:
    january = [
        t
        for t in WORLD.transactions
        if t.is_current
        and t.status == "booked"
        and t.account_id == "A-DE-EUR-1"
        and t.amount.amount < 0
        and "2026-01-01" <= t.booking_date.isoformat() < "2026-02-01"
    ]
    expected = sum((t.amount.amount for t in january), Decimal(0))
    spec = {
        "store": "treasury.transactions",
        "select": [{"column": "transactions.amount", "agg": "sum", "alias": "total"}],
        "filters": [
            {"column": "transactions.account_id", "op": "=", "values": ["A-DE-EUR-1"]},
            {"column": "transactions.amount", "op": "<", "values": ["0"]},
            {"column": "transactions.booking_date", "op": ">=", "values": ["2026-01-01"]},
            {"column": "transactions.booking_date", "op": "<", "values": ["2026-02-01"]},
        ],
        "limit": 10,
    }
    async with _agent(
        [route(), plan(spec), narrate(f"The net outflow was {expected}.")], pg_ledger
    ) as (agent, _):
        answer = await agent.run(
            "Total outflow from A-DE-EUR-1 in January 2026?", await _ctx("ana.analyst")
        )
    assert answer.status is AnswerStatus.ANSWERED and answer.table is not None
    assert Decimal(str(answer.table.rows[0][0])) == expected
    assert answer.sql is not None and "is_current" in answer.sql  # the filters were injected


async def test_cash_is_reported_per_currency_never_added_across_them(
    pg_ledger: PostgresLedger,
) -> None:
    spec = {
        "store": "treasury.daily_balances",
        "select": [
            {"column": "daily_balances.currency"},
            {"column": "daily_balances.closing", "agg": "sum", "alias": "total"},
        ],
        "filters": [{"column": "daily_balances.balance_date", "op": "=", "values": ["2026-06-30"]}],
        "limit": 10,
    }
    totals: dict[str, Decimal] = {}
    for b in WORLD.daily_balances:
        if b.balance_date.isoformat() == "2026-06-30":
            totals[b.closing.currency] = (
                totals.get(b.closing.currency, Decimal(0)) + b.closing.amount
            )
    async with _agent([route(), plan(spec), narrate("Cash differs by currency.")], pg_ledger) as (
        agent,
        _,
    ):
        answer = await agent.run(
            "What is our cash balance at 30 June 2026?", await _ctx("maria.manager")
        )
    assert answer.table is not None
    got = {row[0]: Decimal(str(row[1])) for row in answer.table.rows}
    assert got == totals and len(got) == 3


async def test_a_question_outside_the_users_scope_returns_nothing_not_someone_elses_data(
    pg_ledger: PostgresLedger,
) -> None:
    spec = {
        "store": "treasury.daily_balances",
        "select": [{"column": "daily_balances.closing"}],
        "filters": [
            {"column": "daily_balances.account_id", "op": "=", "values": ["A-UK-GBP-1"]},
            {"column": "daily_balances.balance_date", "op": "=", "values": ["2026-06-30"]},
        ],
        "limit": 10,
    }
    async with _agent([route(), plan(spec)], pg_ledger) as (agent, _):
        answer = await agent.run("Balance of A-UK-GBP-1 on 30 June?", await _ctx("ana.analyst"))
    assert answer.status is AnswerStatus.ANSWERED
    assert answer.table is not None and answer.table.rows == []
    assert "no rows" in (answer.text or "").lower()


async def test_remittance_text_with_an_injected_instruction_is_shown_as_data_and_obeyed_by_no_one(
    pg_ledger: PostgresLedger,
) -> None:
    spec = {
        "store": "treasury.transactions",
        "select": [
            {"column": "transactions.transaction_id"},
            {"column": "transactions.remittance_text"},
        ],
        "filters": [
            {"column": "transactions.account_id", "op": "=", "values": ["A-UK-GBP-1"]},
            {"column": "transactions.booking_date", "op": "=", "values": ["2026-05-20"]},
        ],
        "limit": 50,
    }
    async with _agent(
        [route(), plan(spec), narrate("Two notes contain instructions, shown as data.")], pg_ledger
    ) as (agent, model):
        answer = await agent.run(
            "Show the remittance text on A-UK-GBP-1 on 20 May.", await _ctx("ben.analyst")
        )
    assert answer.status is AnswerStatus.ANSWERED and answer.table is not None
    cells = [str(row[1]) for row in answer.table.rows]
    assert any("IGNORE ALL PREVIOUS INSTRUCTIONS" in c for c in cells)
    assert all(c.startswith("<untrusted source=") for c in cells)
    assert answer.actions == () and answer.usage.tool_calls == 1
    narrator_prompt = " ".join(m.content for m in model.requests[-1].messages)
    assert "<untrusted" in narrator_prompt


async def test_a_fan_out_plan_is_refused_by_code_and_repaired(pg_ledger: PostgresLedger) -> None:
    bad = copy.deepcopy(COUNT)
    bad["select"] = [{"column": "transactions.amount", "agg": "sum", "alias": "t"}]
    bad["joins"] = ["transactions.ssis"]
    async with _agent([route(), plan(bad), plan(COUNT), narrate("There are some.")], pg_ledger) as (
        agent,
        model,
    ):
        answer = await agent.run(
            "How many booked transactions are there?", await _ctx("maria.manager")
        )
    assert answer.status is AnswerStatus.ANSWERED
    assert "FAN_OUT" in " ".join(m.content for m in model.requests[2].messages)


async def test_a_role_without_access_is_refused_and_every_call_is_in_the_ledger(
    pg_ledger: PostgresLedger,
) -> None:
    async with _agent([route(), plan(COUNT)], pg_ledger) as (agent, _):
        answered = await agent.run("How many booked?", await _ctx("amit.approver"))
    assert answered.status is AnswerStatus.REFUSED and answered.refusal_reason == "not_permitted"
    entries = pg_ledger.read_all()
    assert pg_ledger.verify_chain() and entries[-1].decision == "DENY"
    assert entries[-1].reason_code == "ROLE_REQUIRED"


async def test_the_caps_hold_against_a_planner_that_never_gets_it_right(
    pg_ledger: PostgresLedger,
) -> None:
    bad = copy.deepcopy(COUNT)
    bad["select"][0]["column"] = "transactions.invented"
    caps = RunCaps(max_repair_attempts=2)
    async with _agent([route(), *[plan(bad)] * 6], pg_ledger, caps) as (agent, _model):
        answer = await agent.run("How many?", await _ctx("ana.analyst"))
    assert answer.status is AnswerStatus.FAILED and answer.usage.tool_calls == 0
    assert answer.usage.model_calls == 1 + caps.max_plan_attempts


async def test_a_result_cut_short_by_the_row_cap_says_so(pg_ledger: PostgresLedger) -> None:
    # Three currencies exist on this date. With a cap of two, the answer must not look complete.
    spec = {
        "store": "treasury.daily_balances",
        "select": [
            {"column": "daily_balances.currency"},
            {"column": "daily_balances.closing", "agg": "sum", "alias": "total"},
        ],
        "filters": [{"column": "daily_balances.balance_date", "op": "=", "values": ["2026-06-30"]}],
        "limit": 2,
    }
    async with _agent([route(), plan(spec), narrate("Two currencies shown.")], pg_ledger) as (
        agent,
        _,
    ):
        answer = await agent.run("Cash by currency at 30 June 2026?", await _ctx("maria.manager"))
    assert answer.table is not None
    assert len(answer.table.rows) == 2
    assert answer.table.truncated is True
    assert any("truncated" in caveat for caveat in answer.caveats)
