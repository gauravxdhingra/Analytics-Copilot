"""Reference Query API (ARCHITECTURE.md §8.2). Runs verified SQL as the requesting user."""

import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from copilot_auth import AuthError, AuthErrorCode, JwksKeyProvider, Principal, TokenValidator
from copilot_tracing import Tracing
from fastapi import Depends, FastAPI, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from query_api.config import Settings
from query_api.db import Executor, QueryFailed
from query_api.guard import QueryRejected, check_sql
from query_api.params import translate_params
from query_api.provision import user_role

MAX_ROWS = 5000
MAX_TIMEOUT_MS = 30_000

_audit = logging.getLogger("query_api.audit")

_STATUS = {
    "NOT_SELECT": 400,
    "SYNTAX": 400,
    "BAD_PARAMS": 400,
    "UNKNOWN_OBJECT": 400,
    "UNKNOWN_DATABASE": 400,
    "BAD_REQUEST": 422,
    "UNAUTHENTICATED": 401,
    "PERMISSION": 403,
    "TIMEOUT": 504,
    "UNAVAILABLE": 503,
    "INTERNAL": 500,
}
# Fixed text per code: nothing from the database or the request is ever echoed back.
_MESSAGE = {
    "NOT_SELECT": "Only a single read-only SELECT is allowed.",
    "SYNTAX": "The SQL could not be parsed.",
    "BAD_PARAMS": "The parameters do not match the query.",
    "UNKNOWN_OBJECT": "The query refers to an unknown table or column.",
    "UNKNOWN_DATABASE": "Unknown database.",
    "BAD_REQUEST": "The request is malformed.",
    "UNAUTHENTICATED": "Authentication is required.",
    "PERMISSION": "Not permitted.",
    "TIMEOUT": "The query exceeded its time limit.",
    "UNAVAILABLE": "The service is temporarily unavailable.",
    "INTERNAL": "Internal error.",
}


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: str
    sql: str = Field(min_length=1, max_length=20_000)
    params: list[str | int | bool | None] = Field(default_factory=list, max_length=100)
    purpose: str = Field(min_length=1, max_length=200)
    max_rows: int = Field(default=MAX_ROWS, ge=1, le=MAX_ROWS)
    timeout_ms: int = Field(default=MAX_TIMEOUT_MS, ge=1, le=MAX_TIMEOUT_MS)
    client_query_hash: str | None = Field(default=None, max_length=100)


def _error(code: str) -> JSONResponse:
    return JSONResponse(
        status_code=_STATUS[code], content={"error_code": code, "message": _MESSAGE[code]}
    )


class _Reject(Exception):  # internal control flow
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _jsonable(value: Any) -> Any:  # arbitrary database value
    if isinstance(value, Decimal):
        return str(value)  # exact; a float would lose precision on money
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def create_app(
    settings: Settings, validator: TokenValidator | None = None, tracing: Tracing | None = None
) -> FastAPI:
    token_validator = validator or TokenValidator(
        issuer=settings.issuer,
        audience=settings.audience,
        keys=JwksKeyProvider(settings.jwks_url),
    )
    executor = Executor(settings.dsn, max_size=settings.pool_max_size)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await executor.open()
        try:
            yield
        finally:
            await executor.close()

    app = FastAPI(title="Query API (reference)", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.exception_handler(_Reject)
    async def _on_reject(_request: Request, exc: _Reject) -> JSONResponse:
        return _error(exc.code)

    @app.exception_handler(RequestValidationError)
    async def _on_invalid(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return _error("BAD_REQUEST")  # the default body would echo the SQL and parameters

    async def authenticate(authorization: Annotated[str | None, Header()] = None) -> Principal:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise _Reject("UNAUTHENTICATED")
        try:
            return await run_in_threadpool(token_validator.validate, token)
        except AuthError as exc:
            if exc.code is AuthErrorCode.IDP_UNAVAILABLE:
                raise _Reject("UNAVAILABLE") from None  # fail closed, never degrade to open
            raise _Reject("UNAUTHENTICATED") from None

    Caller = Annotated[Principal, Depends(authenticate)]

    def role_for(principal: Principal) -> str:
        role = user_role(principal.subject)
        if role is None:
            raise _Reject("PERMISSION")
        return role

    def prepare(body: QueryRequest) -> tuple[str, dict[str, Any]]:
        if body.database != settings.database_name:
            raise _Reject("UNKNOWN_DATABASE")
        try:
            check_sql(body.sql)
            statement, count = translate_params(body.sql)
        except QueryRejected as exc:
            raise _Reject(exc.code) from None
        except ValueError:
            raise _Reject("BAD_PARAMS") from None
        if len(body.params) != count:
            raise _Reject("BAD_PARAMS")
        return statement, {f"p{i + 1}": v for i, v in enumerate(body.params)}

    def audit(
        principal: Principal,
        body: QueryRequest,
        request: Request,
        *,
        outcome: str,
        row_count: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        # Hash, not text: the audit line never carries the SQL, parameters or tokens.
        _audit.info(
            json.dumps(
                {
                    "event": "query",
                    "subject": principal.subject,
                    "tenant_id": principal.tenant_id,
                    "purpose": body.purpose,
                    "query_hash": "sha256:" + hashlib.sha256(body.sql.encode()).hexdigest(),
                    "client_query_hash": body.client_query_hash,
                    "outcome": outcome,
                    "row_count": row_count,
                    "duration_ms": duration_ms,
                    "traceparent": request.headers.get("traceparent"),
                }
            )
        )

    async def execute(
        principal: Principal, body: QueryRequest, request: Request, *, explain: bool
    ) -> Any:
        with ExitStack() as stack:
            span = None
            if tracing is not None:
                # Continue the caller's trace so a warehouse query links back to the conversation.
                stack.enter_context(tracing.attach(dict(request.headers)))
                span = stack.enter_context(
                    tracing.span(
                        "query.execute",
                        **{
                            "copilot.user_id": principal.subject,
                            "copilot.purpose": body.purpose,
                            "copilot.query_hash": "sha256:"
                            + hashlib.sha256(body.sql.encode()).hexdigest()[:16],
                            "copilot.explain": explain,
                        },
                    )
                )
            try:
                statement, params = prepare(body)
                result = await executor.run(
                    role_for(principal),
                    statement,
                    params,
                    max_rows=body.max_rows,
                    timeout_ms=body.timeout_ms,
                    explain=explain,
                )
            except _Reject as exc:
                audit(principal, body, request, outcome=exc.code)
                if span is not None:
                    span.set_attribute("copilot.outcome", exc.code)
                raise
            except QueryFailed as exc:
                audit(principal, body, request, outcome=exc.code)
                if span is not None:
                    span.set_attribute("copilot.outcome", exc.code)
                raise _Reject(exc.code) from None
            audit(
                principal,
                body,
                request,
                outcome="ok",
                row_count=len(result.rows),
                duration_ms=result.duration_ms,
            )
            if span is not None:
                span.set_attribute("copilot.outcome", "ok")
                span.set_attribute("copilot.row_count", len(result.rows))
                span.set_attribute("copilot.truncated", result.truncated)
        return result

    @app.post("/v1/query")
    async def query(body: QueryRequest, request: Request, principal: Caller) -> JSONResponse:
        result = await execute(principal, body, request, explain=False)
        return JSONResponse(
            {
                "query_id": str(uuid.uuid4()),
                "columns": result.columns,
                "rows": _jsonable(result.rows),
                "row_count": len(result.rows),
                "truncated": result.truncated,
                "as_of": result.as_of,
                "duration_ms": result.duration_ms,
            }
        )

    @app.post("/v1/explain")
    async def explain(body: QueryRequest, request: Request, principal: Caller) -> JSONResponse:
        result = await execute(principal, body, request, explain=True)
        plan = _jsonable(result.rows[0][0])
        return JSONResponse({"plan": plan, "total_cost": plan[0]["Plan"]["Total Cost"]})

    async def fixed(principal: Principal, statement: str) -> list[tuple[Any, ...]]:
        try:
            return await executor.fixed(role_for(principal), statement)
        except QueryFailed as exc:
            raise _Reject(exc.code) from None

    @app.get("/v1/schema")
    async def schema(principal: Caller) -> JSONResponse:
        rows = await fixed(
            principal,
            "SELECT table_schema, table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema = 'treasury' "
            "ORDER BY table_name, ordinal_position",
        )
        tables: dict[str, list[dict[str, str]]] = {}
        for table_schema, table, column, data_type in rows:
            tables.setdefault(f"{table_schema}.{table}", []).append(
                {"name": column, "type": data_type}
            )
        return JSONResponse(
            {
                "database": settings.database_name,
                "tables": [
                    {"schema": key.split(".")[0], "table": key.split(".")[1], "columns": cols}
                    for key, cols in tables.items()
                ],
            }
        )

    @app.get("/v1/freshness")
    async def freshness(principal: Caller) -> JSONResponse:
        rows = await fixed(principal, "SELECT max(loaded_at) FROM treasury.transactions")
        as_of = rows[0][0].isoformat() if rows and rows[0][0] else None
        return JSONResponse({"database": settings.database_name, "as_of": as_of})

    return app
