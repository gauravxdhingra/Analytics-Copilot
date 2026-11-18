"""The decision store: redacted prompts and completions, keyed by span id.

Spans hold hashes and counts only. The text a model saw and produced is kept here, redacted, so
a run can be reviewed and replayed without putting content into trace backends.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import psycopg

from copilot_tracing.redact import redact_ibans

_KINDS = ("prompt", "completion")


def redact_for_store(text: str) -> str:
    return redact_ibans(text)


@dataclass(frozen=True)
class DecisionRecord:
    trace_id: str
    span_id: str
    kind: str
    content: str
    ts: datetime


class DecisionStore(Protocol):
    def save(self, trace_id: str, span_id: str, kind: str, content: str) -> None: ...


def _prepare(kind: str, content: str, max_chars: int) -> str:
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {_KINDS}")
    cleaned = redact_for_store(content)
    if len(cleaned) > max_chars:
        return cleaned[:max_chars] + f"... [truncated {len(cleaned) - max_chars} chars]"
    return cleaned


class InMemoryDecisionStore:
    def __init__(self, *, max_chars: int = 20_000) -> None:
        self._max = max_chars
        self._records: list[DecisionRecord] = []

    def save(self, trace_id: str, span_id: str, kind: str, content: str) -> None:
        self._records.append(
            DecisionRecord(
                trace_id, span_id, kind, _prepare(kind, content, self._max), datetime.now(UTC)
            )
        )

    def for_span(self, span_id: str) -> list[DecisionRecord]:
        return [r for r in self._records if r.span_id == span_id]

    def for_trace(self, trace_id: str) -> list[DecisionRecord]:
        return [r for r in self._records if r.trace_id == trace_id]


_DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.decisions (
    id         bigserial PRIMARY KEY,
    trace_id   text NOT NULL,
    span_id    text NOT NULL,
    kind       text NOT NULL CHECK (kind IN ('prompt', 'completion')),
    content    text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS decisions_span ON ops.decisions (span_id);
CREATE INDEX IF NOT EXISTS decisions_trace ON ops.decisions (trace_id);
CREATE OR REPLACE FUNCTION ops.decisions_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ops.decisions is append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS decisions_immutable ON ops.decisions;
CREATE TRIGGER decisions_immutable BEFORE UPDATE OR DELETE ON ops.decisions
    FOR EACH ROW EXECUTE FUNCTION ops.decisions_reject_change();
"""


def ensure_decisions_schema(owner_dsn: str, *, app_role: str, reset: bool = False) -> None:
    """Create the append-only table and let `app_role` insert and read, nothing more."""
    with psycopg.connect(owner_dsn) as conn:
        if reset:
            conn.execute("DROP TABLE IF EXISTS ops.decisions")
        conn.execute(_DDL)
        role = psycopg.sql.Identifier(app_role)
        conn.execute(psycopg.sql.SQL("GRANT USAGE ON SCHEMA ops TO {}").format(role))
        conn.execute(psycopg.sql.SQL("REVOKE ALL ON ops.decisions FROM {}").format(role))
        conn.execute(psycopg.sql.SQL("GRANT SELECT, INSERT ON ops.decisions TO {}").format(role))
        conn.execute(
            psycopg.sql.SQL("GRANT USAGE ON SEQUENCE ops.decisions_id_seq TO {}").format(role)
        )


class PostgresDecisionStore:
    def __init__(self, dsn: str, *, max_chars: int = 20_000, connect_timeout: int = 5) -> None:
        self._dsn = dsn
        self._max = max_chars
        self._timeout = connect_timeout

    def save(self, trace_id: str, span_id: str, kind: str, content: str) -> None:
        text = _prepare(kind, content, self._max)
        try:
            with psycopg.connect(self._dsn, connect_timeout=self._timeout) as conn:
                conn.execute(
                    "INSERT INTO ops.decisions (trace_id, span_id, kind, content) "
                    "VALUES (%s, %s, %s, %s)",
                    (trace_id, span_id, kind, text),
                )
        except psycopg.Error:
            # Review data is best effort: losing a record must not break the run.
            return

    def for_span(self, span_id: str) -> list[DecisionRecord]:
        with psycopg.connect(self._dsn, connect_timeout=self._timeout) as conn:
            rows = conn.execute(
                "SELECT trace_id, span_id, kind, content, created_at FROM ops.decisions "
                "WHERE span_id = %s ORDER BY id",
                (span_id,),
            ).fetchall()
        return [DecisionRecord(*row) for row in rows]
