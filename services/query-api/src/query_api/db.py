"""Run one query as one user, inside a read-only transaction, with hard resource limits."""

import logging
import time
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql
from psycopg_pool import AsyncConnectionPool, PoolTimeout

_audit_restricted = logging.getLogger("query_api.restricted")


class QueryFailed(Exception):  # carries a mapped code, never raw database text
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class QueryResult:
    columns: list[dict[str, str]]
    rows: list[list[Any]]
    truncated: bool
    as_of: str | None
    duration_ms: int


def _map_error(exc: Exception) -> QueryFailed:
    """Map a database error to a code. Raw messages go only to the restricted logger."""
    sqlstate = getattr(exc, "sqlstate", None)
    _audit_restricted.warning("database error class=%s sqlstate=%s", type(exc).__name__, sqlstate)
    if isinstance(exc, psycopg.errors.QueryCanceled | psycopg.errors.LockNotAvailable):
        return QueryFailed("TIMEOUT")
    if isinstance(exc, psycopg.errors.InsufficientPrivilege):
        return QueryFailed("PERMISSION")
    if isinstance(exc, psycopg.errors.InvalidParameterValue):
        return QueryFailed("PERMISSION")  # SET ROLE to a role that does not exist
    if isinstance(exc, psycopg.errors.SyntaxError):
        return QueryFailed("SYNTAX")
    if isinstance(exc, psycopg.errors.UndefinedTable | psycopg.errors.UndefinedColumn):
        return QueryFailed("UNKNOWN_OBJECT")
    if isinstance(exc, psycopg.errors.DataError | psycopg.errors.ProgrammingError):
        return QueryFailed("BAD_PARAMS")
    if isinstance(exc, psycopg.OperationalError | PoolTimeout):
        return QueryFailed("UNAVAILABLE")
    return QueryFailed("INTERNAL")


class Executor:
    def __init__(self, dsn: str, *, max_size: int = 5) -> None:
        self._pool = AsyncConnectionPool(
            dsn,
            min_size=1,
            max_size=max_size,
            timeout=5.0,
            open=False,
            kwargs={"autocommit": True},
        )

    async def open(self) -> None:
        await self._pool.open()

    async def close(self) -> None:
        await self._pool.close()

    async def run(
        self,
        role: str,
        statement: str,
        params: dict[str, Any],
        *,
        max_rows: int,
        timeout_ms: int,
        explain: bool = False,
    ) -> QueryResult:
        started = time.monotonic()
        try:
            async with self._pool.connection() as conn:
                try:
                    return await self._run(
                        conn, role, statement, params, max_rows, timeout_ms, explain, started
                    )
                finally:
                    # Always end the transaction: nothing here ever needs to commit.
                    await conn.execute("ROLLBACK")
        except QueryFailed:
            raise
        except Exception as exc:
            raise _map_error(exc) from None

    async def _run(
        self,
        conn: psycopg.AsyncConnection,
        role: str,
        statement: str,
        params: dict[str, Any],
        max_rows: int,
        timeout_ms: int,
        explain: bool,
        started: float,
    ) -> QueryResult:
        # Repeatable read gives every statement in one answer the same snapshot.
        await conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        await conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        await conn.execute(
            sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(timeout_ms))
        )
        await conn.execute(
            sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(min(timeout_ms, 5000)))
        )
        await conn.execute("SET LOCAL idle_in_transaction_session_timeout = 30000")
        await conn.execute("SET LOCAL search_path = pg_catalog")
        await conn.execute("SET LOCAL TIME ZONE 'UTC'")

        as_of_row = await (
            await conn.execute("SELECT max(loaded_at) FROM treasury.transactions")
        ).fetchone()
        as_of = as_of_row[0].isoformat() if as_of_row and as_of_row[0] else None

        if explain:
            # A plain cursor: a server-side cursor cannot wrap EXPLAIN. EXPLAIN without ANALYZE
            # plans the statement but never runs it.
            text = sql.SQL("EXPLAIN (FORMAT JSON) ") + sql.SQL(statement)
            cursor = await conn.execute(text, params)
            fetched = await cursor.fetchmany(max_rows + 1)
            description = cursor.description or []
            columns = [
                {"name": c.name, "type": conn.adapters.types[c.type_code].name} for c in description
            ]
        else:
            async with conn.cursor(name="q") as named:
                await named.execute(statement, params)
                fetched = await named.fetchmany(max_rows + 1)
                description = named.description or []
                columns = [
                    {"name": c.name, "type": conn.adapters.types[c.type_code].name}
                    for c in description
                ]
        truncated = len(fetched) > max_rows
        rows = [list(r) for r in fetched[:max_rows]]
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=truncated,
            as_of=as_of,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def fixed(
        self, role: str, statement: str, params: tuple[Any, ...] = ()
    ) -> list[tuple[Any, ...]]:
        """Run one of our own fixed statements (schema, freshness) as the user."""
        try:
            async with self._pool.connection() as conn:
                try:
                    await conn.execute("BEGIN READ ONLY")
                    await conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                    await conn.execute("SET LOCAL statement_timeout = 5000")
                    cursor = await conn.execute(statement, params)
                    return [tuple(r) for r in await cursor.fetchall()]
                finally:
                    await conn.execute("ROLLBACK")
        except Exception as exc:
            raise _map_error(exc) from None
