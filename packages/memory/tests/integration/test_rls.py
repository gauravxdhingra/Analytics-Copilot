"""The database enforces whose rows are whose. A missing WHERE clause must not leak."""

from typing import Any

import psycopg
import pytest
from copilot_memory import Owner, Turn
from copilot_memory.postgres import PostgresMemoryStore

pytestmark = pytest.mark.integration

ANA = Owner("halcyon", "ana")
BEN = Owner("halcyon", "ben")


def _turn(question: str) -> Turn:
    return Turn(conversation_id="conv-0001-aaaa", question=question, status="answered", summary="s")


def _rows(
    dsn: str, sql: str, tenant: str | None = None, user: str | None = None
) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as conn, conn.transaction():
        if user is not None:
            conn.execute("SELECT set_config('memory.tenant_id', %s, true)", (tenant,))
            conn.execute("SELECT set_config('memory.user_id', %s, true)", (user,))
        return conn.execute(sql).fetchall()


@pytest.fixture
def two_users(memory_dsn: str) -> str:
    store = PostgresMemoryStore(memory_dsn)
    store.save_turn(ANA, _turn("ana asked"))
    store.save_turn(BEN, _turn("ben asked"))
    store.add_preference(ANA, "preference", None, "ana only")
    return memory_dsn


def test_a_session_that_names_no_user_sees_nothing(two_users: str) -> None:
    assert _rows(two_users, "SELECT question FROM memory.turns") == []
    assert _rows(two_users, "SELECT text FROM memory.preferences") == []


def test_a_session_sees_only_the_user_it_names_even_without_a_where_clause(two_users: str) -> None:
    got = _rows(two_users, "SELECT question FROM memory.turns", "halcyon", "ben")
    assert got == [("ben asked",)]


def test_the_same_user_id_in_another_tenant_sees_nothing(two_users: str) -> None:
    assert _rows(two_users, "SELECT question FROM memory.turns", "other", "ana") == []


def test_a_session_cannot_write_a_row_for_someone_else(two_users: str) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        psycopg.connect(two_users) as conn,
        conn.transaction(),
    ):
        conn.execute("SELECT set_config('memory.tenant_id', 'halcyon', true)")
        conn.execute("SELECT set_config('memory.user_id', 'ana', true)")
        conn.execute(
            "INSERT INTO memory.turns (tenant_id, user_id, conversation_id, question, status,"
            " summary, expires_at) VALUES ('halcyon', 'ben', 'conv-0001-aaaa', 'forged', 'x',"
            " 's', now() + interval '1 day')"
        )


def test_the_memory_role_can_read_nothing_else_in_the_database(two_users: str) -> None:
    for table in ("treasury.transactions", "ops.drafts", "ops.ledger"):
        with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedTable)):
            _rows(two_users, f"SELECT 1 FROM {table} LIMIT 1")


def test_a_turn_cannot_be_edited_only_replaced_or_expired(two_users: str) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        psycopg.connect(two_users) as conn,
        conn.transaction(),
    ):
        conn.execute("SELECT set_config('memory.tenant_id', 'halcyon', true)")
        conn.execute("SELECT set_config('memory.user_id', 'ana', true)")
        conn.execute("UPDATE memory.turns SET question = 'rewritten'")
