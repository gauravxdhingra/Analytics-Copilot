"""Memory in Postgres.

Every call names its owner to the database, which then shows only that owner's rows (row-level
security), so a forgotten WHERE clause cannot leak.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from copilot_memory.models import (
    MAX_PREFERENCES,
    MAX_TURNS_PER_USER,
    TURN_TTL_SECONDS,
    MemoryFailure,
    Owner,
    Preference,
    Turn,
)
from copilot_memory.store import Kind, check_new_preference

_RECENT_SQL = (
    "SELECT conversation_id, question, status, plan, summary, trace_id, created_at"
    " FROM memory.turns WHERE expires_at > now() AND conversation_id = %s"
    " ORDER BY turn_id DESC LIMIT %s"
)
_HISTORY_SQL = (
    "SELECT conversation_id, question, status, plan, summary, trace_id, created_at"
    " FROM memory.turns WHERE expires_at > now() ORDER BY turn_id DESC LIMIT %s"
)


def _failure(exc: psycopg.Error) -> MemoryFailure:
    # Raw database text never leaves this module.
    return MemoryFailure("UNAVAILABLE")


class PostgresMemoryStore:
    def __init__(
        self, dsn: str, *, connect_timeout: int = 5, ttl_seconds: float = TURN_TTL_SECONDS
    ) -> None:
        self._dsn = dsn
        self._timeout = connect_timeout
        self._ttl = ttl_seconds

    @contextmanager
    def _scoped(self, owner: Owner) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        """A transaction that names its owner to the database. Always closed, even on failure."""
        try:
            with (
                psycopg.connect(
                    self._dsn, connect_timeout=self._timeout, row_factory=dict_row
                ) as conn,
                conn.transaction(),
            ):
                conn.execute("SELECT set_config('memory.tenant_id', %s, true)", (owner.tenant_id,))
                conn.execute("SELECT set_config('memory.user_id', %s, true)", (owner.user_id,))
                yield conn
        except psycopg.Error as exc:
            raise _failure(exc) from None

    # -- turns -----------------------------------------------------------------------------------

    def save_turn(self, owner: Owner, turn: Turn) -> None:
        with self._scoped(owner) as conn:
            conn.execute("DELETE FROM memory.turns WHERE expires_at <= now()")
            conn.execute(
                "INSERT INTO memory.turns (tenant_id, user_id, conversation_id, question, status,"
                " plan, summary, trace_id, expires_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now() + make_interval(secs => %s))",
                (
                    owner.tenant_id,
                    owner.user_id,
                    turn.conversation_id,
                    turn.question,
                    turn.status,
                    Jsonb(turn.plan) if turn.plan is not None else None,
                    turn.summary,
                    turn.trace_id,
                    self._ttl,
                ),
            )
            conn.execute(
                "DELETE FROM memory.turns WHERE turn_id IN ("
                " SELECT turn_id FROM memory.turns ORDER BY turn_id DESC OFFSET %s)",
                (MAX_TURNS_PER_USER,),
            )

    def recent_turns(self, owner: Owner, conversation_id: str, limit: int) -> list[Turn]:
        with self._scoped(owner) as conn:
            rows = conn.execute(_RECENT_SQL, (conversation_id, limit)).fetchall()
        return [Turn.model_validate(r) for r in reversed(rows)]

    def history(self, owner: Owner, limit: int) -> list[Turn]:
        with self._scoped(owner) as conn:
            rows = conn.execute(_HISTORY_SQL, (limit,)).fetchall()
        return [Turn.model_validate(r) for r in rows]

    def clear_history(self, owner: Owner) -> int:
        with self._scoped(owner) as conn:
            return conn.execute("DELETE FROM memory.turns").rowcount

    # -- long-term -------------------------------------------------------------------------------

    def list_preferences(self, owner: Owner) -> list[Preference]:
        with self._scoped(owner) as conn:
            rows = conn.execute(
                "SELECT pref_id::text, kind, name, text, created_at, updated_at"
                " FROM memory.preferences ORDER BY created_at, pref_id"
            ).fetchall()
        return [Preference.model_validate(r) for r in rows]

    def add_preference(self, owner: Owner, kind: Kind, name: str | None, text: str) -> Preference:
        clean_name, clean_text = check_new_preference(kind, name, text)
        with self._scoped(owner) as conn:
            if kind == "saved_definition":
                conn.execute(
                    "DELETE FROM memory.preferences WHERE kind = 'saved_definition' AND name = %s",
                    (clean_name,),
                )
            count = conn.execute("SELECT count(*) AS n FROM memory.preferences").fetchone()
            if count is not None and count["n"] >= MAX_PREFERENCES:
                raise MemoryFailure("LIMIT_REACHED")
            row = conn.execute(
                "INSERT INTO memory.preferences (tenant_id, user_id, kind, name, text)"
                " VALUES (%s, %s, %s, %s, %s)"
                " RETURNING pref_id::text, kind, name, text, created_at, updated_at",
                (owner.tenant_id, owner.user_id, kind, clean_name, clean_text),
            ).fetchone()
        return Preference.model_validate(row)

    def delete_preference(self, owner: Owner, pref_id: str) -> bool:
        try:
            uuid.UUID(pref_id)
        except ValueError:
            return False  # not an id at all, so nothing to delete
        with self._scoped(owner) as conn:
            deleted = conn.execute(
                "DELETE FROM memory.preferences WHERE pref_id = %s::uuid", (pref_id,)
            ).rowcount
        return deleted == 1

    def clear_preferences(self, owner: Owner) -> int:
        with self._scoped(owner) as conn:
            return conn.execute("DELETE FROM memory.preferences").rowcount
