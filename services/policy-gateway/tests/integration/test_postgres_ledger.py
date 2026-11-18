import concurrent.futures

import psycopg
import pytest
from policy_gateway.ledger import LedgerDraft, LedgerError
from policy_gateway.postgres_ledger import PostgresLedger

pytestmark = pytest.mark.integration


def _draft(n: int = 1, decision: str = "ALLOW", reason: str | None = None) -> LedgerDraft:
    return LedgerDraft(
        run_id="run-1",
        trace_id="trace-1",
        tenant_id="halcyon",
        agent_id="insights.query_planner",
        user_id="u-1",
        tool="analytics.describe_store",
        args_hash=f"sha256:{n:064d}",
        args_redacted={"store_id": f"t{n}"},
        decision=decision,
        reason_code=reason,
        policy_version=3,
        result_hash=None if decision == "DENY" else f"sha256:{n:064x}",
    )


def test_appended_entries_form_a_valid_chain(pg_ledger: PostgresLedger) -> None:
    first = pg_ledger.append(_draft(1))
    second = pg_ledger.append(_draft(2, "DENY", "TOOL_NOT_ALLOWED"))
    assert (first.seq, second.seq) == (1, 2)
    assert second.prev_hash == first.row_hash
    assert pg_ledger.verify_chain()
    assert [e.reason_code for e in pg_ledger.read_all()] == [None, "TOOL_NOT_ALLOWED"]


def test_the_chain_survives_a_new_connection(pg_ledger: PostgresLedger, gateway_dsn: str) -> None:
    pg_ledger.append(_draft(1))
    other = PostgresLedger(gateway_dsn)
    entry = other.append(_draft(2))
    assert entry.seq == 2 and other.verify_chain()


def test_concurrent_writers_never_fork_the_chain(
    pg_ledger: PostgresLedger, gateway_dsn: str
) -> None:
    def write(i: int) -> int:
        return PostgresLedger(gateway_dsn).append(_draft(i)).seq

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        seqs = sorted(pool.map(write, range(1, 13)))
    assert seqs == list(range(1, 13))
    assert pg_ledger.verify_chain()


def test_the_application_role_cannot_update_or_delete(
    pg_ledger: PostgresLedger, gateway_dsn: str
) -> None:
    pg_ledger.append(_draft(1))
    with psycopg.connect(gateway_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE ops.ledger SET decision = 'ALLOW'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM ops.ledger")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("TRUNCATE ops.ledger")


def test_even_the_owner_is_stopped_by_the_trigger(
    pg_ledger: PostgresLedger, owner: psycopg.Connection
) -> None:
    pg_ledger.append(_draft(1))
    with pytest.raises(psycopg.errors.RaiseException):
        owner.execute("UPDATE ops.ledger SET reason_code = 'EDITED'")
    with pytest.raises(psycopg.errors.RaiseException):
        owner.execute("DELETE FROM ops.ledger")


def test_tampering_that_bypasses_the_trigger_is_detected(
    pg_ledger: PostgresLedger, owner: psycopg.Connection
) -> None:
    for n in range(1, 4):
        pg_ledger.append(_draft(n))
    assert pg_ledger.verify_chain()
    owner.execute("ALTER TABLE ops.ledger DISABLE TRIGGER ledger_immutable")
    try:
        owner.execute("UPDATE ops.ledger SET user_id = 'someone-else' WHERE seq = 2")
    finally:
        owner.execute("ALTER TABLE ops.ledger ENABLE TRIGGER ledger_immutable")
    assert not pg_ledger.verify_chain()


def test_deleting_a_row_breaks_the_chain(
    pg_ledger: PostgresLedger, owner: psycopg.Connection
) -> None:
    for n in range(1, 4):
        pg_ledger.append(_draft(n))
    owner.execute("ALTER TABLE ops.ledger DISABLE TRIGGER ledger_immutable")
    try:
        owner.execute("DELETE FROM ops.ledger WHERE seq = 2")
    finally:
        owner.execute("ALTER TABLE ops.ledger ENABLE TRIGGER ledger_immutable")
    assert not pg_ledger.verify_chain()


def test_an_unreachable_database_is_a_ledger_error_so_the_call_is_denied() -> None:
    dead = PostgresLedger("postgresql://x:y@127.0.0.1:9/none", connect_timeout=1)
    with pytest.raises(LedgerError):
        dead.append(_draft(1))
