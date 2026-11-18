"""Load the world into the local Postgres: `make up` then `make test-integration`."""

import os
from collections.abc import Iterator

import psycopg
import pytest
from halcyon_world import World, generate_world
from halcyon_world.load import load_world

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def dsn() -> str:
    password = os.environ.get("POSTGRES_OWNER_PASSWORD")
    if not password:
        pytest.skip("POSTGRES_OWNER_PASSWORD not set; load deploy/compose/.env")
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    return f"postgresql://copilot_owner:{password}@{host}/analytics"


@pytest.fixture(scope="module")
def world() -> World:
    return generate_world(seed=7)


@pytest.fixture(scope="module")
def conn(dsn: str, world: World) -> Iterator[psycopg.Connection]:
    load_world(world, dsn)
    # Autocommit so this fixture never holds locks that block the reload test's DROP SCHEMA.
    with psycopg.connect(dsn, autocommit=True) as connection:
        yield connection


def _scalar(conn: psycopg.Connection, sql: str) -> object:
    row = conn.execute(sql).fetchone()
    assert row is not None
    return row[0]


def test_row_counts_match_the_manifest(conn: psycopg.Connection, world: World) -> None:
    counts = world.manifest()["counts"]
    for table, key in [
        ("treasury.transactions", "transactions"),
        ("treasury.payments", "payments"),
        ("treasury.accounts", "accounts"),
        ("treasury.ssis", "ssis"),
        ("treasury.fx_rates", "fx_rates"),
        ("treasury.daily_balances", "daily_balances"),
        ("docs.documents", "documents"),
        ("eval.labels", "labels"),
    ]:
        assert _scalar(conn, f"SELECT count(*) FROM {table}") == counts[key], table


def test_sql_balances_agree_with_the_generator(conn: psycopg.Connection) -> None:
    mismatches = _scalar(
        conn,
        """
        SELECT count(*) FROM treasury.daily_balances b
        JOIN treasury.accounts a USING (account_id)
        WHERE b.closing <> a.opening_balance + COALESCE((
            SELECT sum(t.amount) FROM treasury.transactions t
            WHERE t.account_id = b.account_id AND t.is_current AND t.status = 'booked'
              AND t.booking_date <= b.balance_date), 0)
        """,
    )
    assert mismatches == 0


def test_naive_sum_differs_from_the_correct_sum(conn: psycopg.Connection) -> None:
    # This gap is what the verifier and gold-set traps exist to catch.
    naive = _scalar(
        conn, "SELECT sum(amount) FROM treasury.transactions WHERE account_id='A-DE-EUR-1'"
    )
    correct = _scalar(
        conn,
        "SELECT sum(amount) FROM treasury.transactions "
        "WHERE account_id='A-DE-EUR-1' AND is_current AND status='booked'",
    )
    assert naive != correct


def test_database_refuses_two_current_versions(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        conn.execute(
            "INSERT INTO treasury.transactions SELECT transaction_id, 99, true, entity_id, "
            "account_id, counterparty_id, booking_date, value_date, amount, currency, status, "
            "reference, remittance_text, beneficiary_iban, loaded_at "
            "FROM treasury.transactions WHERE is_current LIMIT 1"
        )


def test_database_refuses_one_person_in_two_approval_roles(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute("UPDATE treasury.payments SET checker = maker WHERE payment_id = 'P000001'")


def test_reload_is_idempotent(dsn: str, world: World) -> None:
    first = load_world(world, dsn)
    second = load_world(world, dsn)
    assert first == second
