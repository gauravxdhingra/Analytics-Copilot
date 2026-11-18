"""Client for the Query API. Every call carries a token exchanged for the Query API audience."""

from collections.abc import Callable
from typing import Any, Protocol

import httpx


class UpstreamFailure(Exception):  # carries a mapped code, never raw text
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class QueryApi(Protocol):
    async def query(
        self,
        token: str,
        *,
        sql: str,
        params: list[Any],
        purpose: str,
        max_rows: int,
        timeout_ms: int,
    ) -> dict[str, Any]: ...

    async def schema(self, token: str) -> dict[str, Any]: ...

    async def freshness(self, token: str) -> dict[str, Any]: ...


class HttpQueryApi:
    def __init__(
        self, http: httpx.AsyncClient, inject: Callable[[], dict[str, str]] | None = None
    ) -> None:
        self._http = http
        self._inject = inject  # trace context for the Query API, taken at call time

    async def _send(self, method: str, path: str, token: str, **kwargs: Any) -> dict[str, Any]:
        try:
            headers = {
                "Authorization": f"Bearer {token}",
                **(self._inject() if self._inject else {}),
            }
            response = await self._http.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException:
            raise UpstreamFailure("TIMEOUT") from None
        except httpx.TransportError:
            raise UpstreamFailure("UNAVAILABLE") from None
        try:
            body = response.json()
        except ValueError:
            raise UpstreamFailure("INTERNAL") from None
        if response.status_code != httpx.codes.OK:
            raise UpstreamFailure(str(body.get("error_code", "INTERNAL")))
        return dict(body)

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
        payload = {
            "database": "treasury_analytics",
            "sql": sql,
            "params": params,
            "purpose": purpose,
            "max_rows": max_rows,
            "timeout_ms": timeout_ms,
        }
        return await self._send("POST", "/v1/query", token, json=payload)

    async def schema(self, token: str) -> dict[str, Any]:
        return await self._send("GET", "/v1/schema", token)

    async def freshness(self, token: str) -> dict[str, Any]:
        return await self._send("GET", "/v1/freshness", token)
