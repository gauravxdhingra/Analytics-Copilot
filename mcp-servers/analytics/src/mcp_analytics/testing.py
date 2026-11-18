"""Test doubles for the analytics server."""

from dataclasses import dataclass
from typing import Any

from mcp_analytics.query_client import UpstreamFailure

AS_OF = "2026-06-30T23:00:00+00:00"


@dataclass(frozen=True)
class QueryCall:
    token: str
    sql: str
    params: list[Any]
    purpose: str
    max_rows: int


class FakeQueryApi:
    def __init__(self) -> None:
        self.calls: list[QueryCall] = []
        self._failure: str | None = None

    def fail_with(self, code: str) -> None:
        self._failure = code

    async def query(
        self,
        token: str,
        *,
        sql: str,
        params: list[Any],
        purpose: str,
        max_rows: int,
        timeout_ms: int,
    ) -> dict[str, Any]:
        self.calls.append(QueryCall(token, sql, params, purpose, max_rows))
        if self._failure:
            raise UpstreamFailure(self._failure)
        return {
            "columns": [
                {"name": "transaction_id", "type": "text"},
                {"name": "remittance_text", "type": "text"},
            ],
            "rows": [["T000001", "INV 2026-1001"], ["T000002", "INV 2026-1002"]],
            "row_count": 2,
            "truncated": False,
            "as_of": AS_OF,
            "duration_ms": 3,
        }

    async def schema(self, token: str) -> dict[str, Any]:
        def cols(*names: tuple[str, str]) -> list[dict[str, str]]:
            return [{"name": n, "type": t} for n, t in names]

        return {
            "database": "treasury_analytics",
            "tables": [
                {
                    "schema": "treasury",
                    "table": "transactions",
                    "columns": cols(("transaction_id", "text"), ("amount", "numeric")),
                },
                {
                    "schema": "treasury",
                    "table": "accounts",
                    "columns": cols(("account_id", "text"), ("currency", "character")),
                },
            ],
        }

    async def freshness(self, token: str) -> dict[str, Any]:
        return {"database": "treasury_analytics", "as_of": AS_OF}


class FakeExchanger:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    async def exchange(self, subject_token: str, audience: str) -> str:
        self.requests.append((subject_token, audience))
        return f"exchanged-for-{audience}"
