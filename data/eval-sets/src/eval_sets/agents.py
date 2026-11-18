"""Agents the harness can evaluate: a ceiling, a floor, and the naive LLM baseline."""

import html
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from copilot_evals.agents import AgentAnswer
from copilot_evals.models import Behavior, Capability, GoldItem
from copilot_models import ModelClient, ModelError, ModelRequest, ModelRole
from copilot_models.port import Message
from pydantic import BaseModel


@dataclass(frozen=True)
class ExecResult:
    rows: list[list[Any]] | None
    error_code: str | None = None
    latency_ms: int = 0


class SqlExecutor(Protocol):
    async def run(self, persona: str, sql: str) -> ExecResult: ...

    async def schema_text(self, persona: str) -> str: ...


class QueryApiExecutor:
    """Runs SQL through the real Query API, so token validation, the SQL guard and row-level
    security are all part of what is evaluated."""

    def __init__(self, http: httpx.AsyncClient, token_for: Any) -> None:
        self._http = http
        self._token_for = token_for

    async def _headers(self, persona: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._token_for(persona)}"}

    async def run(self, persona: str, sql: str) -> ExecResult:
        response = await self._http.post(
            "/v1/query",
            json={
                "database": "treasury_analytics",
                "sql": sql,
                "purpose": "eval",
                "max_rows": 1000,
            },
            headers=await self._headers(persona),
        )
        body = response.json()
        if response.status_code != httpx.codes.OK:
            return ExecResult(rows=None, error_code=str(body.get("error_code", "INTERNAL")))
        return ExecResult(rows=body["rows"], latency_ms=int(body["duration_ms"]))

    async def schema_text(self, persona: str) -> str:
        response = await self._http.get("/v1/schema", headers=await self._headers(persona))
        tables = response.json()["tables"]
        return "\n".join(
            f"{t['schema']}.{t['table']}("
            + ", ".join(f"{c['name']} {c['type']}" for c in t["columns"])
            + ")"
            for t in tables
        )


class OracleAgent:
    """The ceiling: runs the hand-written reference SQL and knows when to refuse.

    It exists to prove the harness and gates can be passed, and that the gold SQL is legal
    through the real Query API. It is not a baseline.
    """

    name = "oracle"

    def __init__(self, executor: SqlExecutor) -> None:
        self._executor = executor

    async def answer(self, item: GoldItem) -> AgentAnswer:
        if item.capability is Capability.DOCUMENTS:
            return AgentAnswer(text=" ".join(item.must_mention))
        if item.behavior is Behavior.CLARIFY:
            return AgentAnswer(clarified=True)
        if item.behavior is Behavior.REFUSE and item.reference_sql is None:
            return AgentAnswer(refused=True)
        assert item.reference_sql is not None  # noqa: S101  # guaranteed by GoldItem validation
        result = await self._executor.run(item.persona, item.reference_sql)
        text = " ".join(item.must_mention)
        return AgentAnswer(
            sql=item.reference_sql,
            rows=result.rows,
            error_code=result.error_code,
            latency_ms=result.latency_ms,
            text=text or None,
        )


class CarelessAgent:
    """The floor: falls into every trap and guesses at questions it should decline.

    It proves the gates discriminate. Where a gold item ships a naive query, it runs that.
    """

    name = "careless"

    def __init__(self, executor: SqlExecutor) -> None:
        self._executor = executor

    async def answer(self, item: GoldItem) -> AgentAnswer:
        if item.capability is Capability.DOCUMENTS:
            return AgentAnswer(text="Here is a summary.")
        if (
            item.behavior is not Behavior.ANSWER
            and item.trap is not None
            and item.reference_sql is None
        ):
            return AgentAnswer(sql="SELECT 42", rows=[["42"]])  # a confident guess
        sql = item.naive_sql or item.reference_sql
        assert sql is not None  # noqa: S101
        result = await self._executor.run(item.persona, sql)
        return AgentAnswer(
            sql=sql, rows=result.rows, error_code=result.error_code, latency_ms=result.latency_ms
        )


class _SqlOut(BaseModel):
    sql: str


_SYSTEM = (
    "You write a single PostgreSQL SELECT query that answers the user's question over the schema "
    "provided. Use schema-qualified table names. Return JSON with one field, sql. "
    "If you cannot write a query, return an empty string."
)


class LlmNaiveAgent:
    """The baseline: schema and question in, SQL out, run as-is.

    No catalogue, no verifier, no grounding and no refusal path. Whatever it scores is the
    floor that the real agent has to beat.
    """

    name = "baseline"
    prompt_version = "naive-sql@1"

    def __init__(self, model: ModelClient, executor: SqlExecutor, *, timeout_seconds: float = 30.0):
        self._model = model
        self._executor = executor
        self._timeout = timeout_seconds

    async def answer(self, item: GoldItem) -> AgentAnswer:
        schema = await self._executor.schema_text(item.persona)
        request = ModelRequest(
            role=ModelRole.PLANNER,
            prompt_version=self.prompt_version,
            system=_SYSTEM,
            messages=[
                Message(role="user", content=f"Schema:\n{schema}\n\nQuestion: {item.question}")
            ],
            output_schema=_SqlOut.model_json_schema(),
            timeout_seconds=self._timeout,
        )
        try:
            response = await self._model.generate(request)
            sql = response.parse(_SqlOut).sql.strip()
        except ModelError as exc:
            return AgentAnswer(error_code=exc.code.value)
        used = response.usage
        if not sql:
            return AgentAnswer(
                refused=True,
                latency_ms=response.latency_ms,
                input_tokens=used.input_tokens,
                output_tokens=used.output_tokens,
                cost_usd=used.cost_usd,
            )
        result = await self._executor.run(item.persona, sql)
        return AgentAnswer(
            sql=sql,
            rows=result.rows,
            text=None if result.rows is None else json.dumps(result.rows),
            error_code=result.error_code,
            latency_ms=response.latency_ms + result.latency_ms,
            input_tokens=used.input_tokens,
            output_tokens=used.output_tokens,
            cost_usd=used.cost_usd,
        )


_UNTRUSTED = re.compile(r'<untrusted source="[^"]*">(.*)</untrusted>', re.DOTALL)


def unwrap_untrusted(value: Any) -> Any:
    """Undo the gateway's data-block wrapper so a presented cell compares to its raw value."""
    if isinstance(value, str):
        match = _UNTRUSTED.fullmatch(value)
        if match:
            return html.unescape(match.group(1).replace("&lt;", "<"))
    return value


class OrchestratorAgent:
    """The product agent, adapted to the eval harness.

    It runs the real orchestrator (router, planner, gateway, verifier, narrator) as the persona
    and maps its answer to what the scorer checks.
    """

    name = "copilot"

    def __init__(self, orchestrator: Any, context_for: Callable[[str], Awaitable[Any]]) -> None:
        self._orchestrator = orchestrator
        self._context_for = context_for

    async def answer(self, item: GoldItem) -> AgentAnswer:
        answer = await self._orchestrator.run(item.question, await self._context_for(item.persona))
        status = answer.status.value
        rows = None
        if answer.table is not None:
            rows = [[unwrap_untrusted(c) for c in row] for row in answer.table.rows]
        failed = status == "failed"
        return AgentAnswer(
            sql=answer.sql,
            rows=rows,
            text=answer.text,
            refused=status == "refused",
            clarified=status == "clarify",
            error_code=answer.refusal_reason if failed else None,
            actions=tuple(answer.actions),
            input_tokens=answer.usage.input_tokens,
            output_tokens=answer.usage.output_tokens,
            cost_usd=answer.usage.cost_usd,
        )
