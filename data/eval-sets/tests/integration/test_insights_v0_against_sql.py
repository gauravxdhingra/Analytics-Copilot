"""The gold set against the real database: SQL, row-level security and the oracle must agree."""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from copilot_evals.compare import results_match
from copilot_evals.loader import load_gold_set
from copilot_evals.models import Behavior, Capability, GoldItem, TrapKind
from eval_sets.insights_v0 import DATA_FILE
from query_api.provision import IdentityUser

pytestmark = pytest.mark.integration

GOLD = load_gold_set(DATA_FILE)
SQL_ITEMS = [i for i in GOLD.items if i.reference_sql is not None]
TRAP_ITEMS = [i for i in GOLD.items if i.naive_sql is not None]
SCOPE_ITEMS = [i for i in GOLD.items if i.trap is TrapKind.SCOPE]


@pytest.fixture
def as_persona(query_dsn: str, identity: list[IdentityUser]) -> Iterator[Any]:
    """Run SQL as a demo user's database role, so row-level security applies exactly as live."""
    roles = {u.username: u.db_role for u in identity}

    def run(persona: str, sql: str) -> list[tuple[Any, ...]]:
        with psycopg.connect(query_dsn, autocommit=True) as conn:
            conn.execute(f"SET ROLE {roles[persona]}")
            conn.execute("SET TIME ZONE 'UTC'")
            return conn.execute(sql).fetchall()

    yield run


@pytest.mark.parametrize("item", SQL_ITEMS, ids=lambda i: i.id)
def test_reference_sql_under_the_personas_scope_matches_the_independent_oracle(
    item: GoldItem, as_persona: Any
) -> None:
    assert item.reference_sql and item.expected_rows is not None
    actual = as_persona(item.persona, item.reference_sql)
    assert results_match(item.expected_rows, actual, ordered=item.ordered), (
        f"{item.id}: expected {item.expected_rows[:3]}, got {actual[:3]}"
    )


@pytest.mark.parametrize("item", TRAP_ITEMS, ids=lambda i: i.id)
def test_the_naive_query_really_falls_into_the_trap(item: GoldItem, as_persona: Any) -> None:
    assert item.naive_sql and item.expected_rows is not None
    naive = as_persona(item.persona, item.naive_sql)
    assert not results_match(item.expected_rows, naive, ordered=item.ordered), (
        f"{item.id}: the naive query returns the right answer, so this is not a trap"
    )


@pytest.mark.parametrize("item", SCOPE_ITEMS, ids=lambda i: i.id)
def test_out_of_scope_data_exists_but_is_invisible_to_the_persona(
    item: GoldItem, as_persona: Any, owner: psycopg.Connection
) -> None:
    assert item.reference_sql
    assert as_persona(item.persona, item.reference_sql) == []
    unrestricted = owner.execute(item.reference_sql).fetchall()
    assert unrestricted, f"{item.id}: nothing exists out of scope, so the test proves nothing"


def test_the_same_question_gets_different_answers_for_different_personas() -> None:
    counts = [i for i in GOLD.items if i.question == "How many booked transactions are there?"]
    assert len(counts) == 3
    assert len({i.expected_rows[0][0] for i in counts if i.expected_rows}) == 3


def test_the_gold_set_contains_documents_items_the_sql_path_skips() -> None:
    docs = [i for i in GOLD.items if i.capability is Capability.DOCUMENTS]
    assert docs and all(i.reference_sql is None for i in docs)
    assert all(i.behavior is Behavior.ANSWER for i in docs)
