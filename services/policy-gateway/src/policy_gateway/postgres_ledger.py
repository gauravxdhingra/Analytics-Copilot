"""Durable, append-only, hash-chained ledger in Postgres."""

import json
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from policy_gateway.ledger import (
    GENESIS,
    LedgerDraft,
    LedgerEntry,
    LedgerError,
    chain_is_valid,
    seal,
)

APP_ROLE = "copilot_gateway"
_LOCK_KEY = 4_815_162_342  # serialises writers so the chain cannot fork

_DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.ledger (
    seq             bigint PRIMARY KEY,
    ts              timestamptz NOT NULL,
    run_id          text NOT NULL,
    trace_id        text NOT NULL,
    tenant_id       text NOT NULL,
    agent_id        text NOT NULL,
    user_id         text NOT NULL,
    tool            text NOT NULL,
    args_hash       text NOT NULL,
    args_redacted   jsonb NOT NULL,
    decision        text NOT NULL CHECK (decision IN ('ALLOW', 'DENY')),
    reason_code     text,
    policy_version  integer NOT NULL,
    result_hash     text,
    prev_hash       text NOT NULL,
    row_hash        text NOT NULL UNIQUE
);
CREATE OR REPLACE FUNCTION ops.ledger_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ops.ledger is append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS ledger_immutable ON ops.ledger;
CREATE TRIGGER ledger_immutable BEFORE UPDATE OR DELETE ON ops.ledger
    FOR EACH ROW EXECUTE FUNCTION ops.ledger_reject_change();
"""


def ensure_schema(owner_dsn: str, *, app_password: str, reset: bool = False) -> None:
    """Create the ledger and its writer role. `reset` drops existing history: tests only."""
    with psycopg.connect(owner_dsn) as conn:
        if reset:
            conn.execute("DROP SCHEMA IF EXISTS ops CASCADE")
        conn.execute(_DDL)
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL(verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                sql.Identifier(APP_ROLE), sql.Literal(app_password)
            )
        )
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA ops TO {}").format(sql.Identifier(APP_ROLE)))
        conn.execute(sql.SQL("REVOKE ALL ON ops.ledger FROM {}").format(sql.Identifier(APP_ROLE)))
        # Insert and read only. No update, delete or truncate, on top of the trigger.
        conn.execute(
            sql.SQL("GRANT SELECT, INSERT ON ops.ledger TO {}").format(sql.Identifier(APP_ROLE))
        )


class PostgresLedger:
    def __init__(self, dsn: str, *, connect_timeout: int = 5) -> None:
        self._dsn = dsn
        self._timeout = connect_timeout

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._dsn, connect_timeout=self._timeout, row_factory=dict_row)

    def append(self, draft: LedgerDraft) -> LedgerEntry:
        try:
            with self._connect() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
                last = conn.execute(
                    "SELECT seq, row_hash FROM ops.ledger ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                seq = (last["seq"] + 1) if last else 1
                prev = last["row_hash"] if last else GENESIS
                entry = seal(draft, seq=seq, prev_hash=prev, ts=datetime.now(UTC))
                conn.execute(
                    "INSERT INTO ops.ledger VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s)",
                    (
                        entry.seq,
                        entry.ts,
                        entry.run_id,
                        entry.trace_id,
                        entry.tenant_id,
                        entry.agent_id,
                        entry.user_id,
                        entry.tool,
                        entry.args_hash,
                        json.dumps(entry.args_redacted),
                        entry.decision,
                        entry.reason_code,
                        entry.policy_version,
                        entry.result_hash,
                        entry.prev_hash,
                        entry.row_hash,
                    ),
                )
                return entry
        except psycopg.Error:
            raise LedgerError("ledger write failed") from None

    def read_all(self) -> list[LedgerEntry]:
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT * FROM ops.ledger ORDER BY seq").fetchall()
        except psycopg.Error:
            raise LedgerError("ledger read failed") from None
        return [LedgerEntry(**{**row, "ts": row["ts"].astimezone(UTC)}) for row in rows]

    def verify_chain(self) -> bool:
        return chain_is_valid(self.read_all())
