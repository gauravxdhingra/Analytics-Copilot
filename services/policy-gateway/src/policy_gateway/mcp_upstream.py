"""The gateway's connection to MCP tool servers over streamable HTTP.

Each call opens a short session with the server's own audience-bound token. Failures become
UpstreamError codes: the gateway then denies the call (fail closed) and never degrades to an
unguarded path.
"""

import re
from collections.abc import Callable
from typing import Any, cast

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import RequestParamsMeta

from policy_gateway.gateway import UpstreamError

_CODE = re.compile(r"[A-Z][A-Z_]{2,40}\b")


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _leaves(sub)]
    return [exc]


def _classify(exc: BaseException) -> UpstreamError:
    for leaf in _leaves(exc):
        if isinstance(leaf, httpx2.TimeoutException | TimeoutError):
            return UpstreamError("UPSTREAM_TIMEOUT")
        if isinstance(leaf, MCPError):
            # A rejected token and a protocol fault look the same on the wire, so treat both as
            # a refusal rather than guess.
            return UpstreamError("UPSTREAM_REJECTED")
    return UpstreamError("UPSTREAM_UNAVAILABLE")


def _tool_error_code(texts: list[str]) -> str:
    """The server's own error text from its first mapped code onward. It never holds raw
    upstream or database text, so passing it on lets the planner correct a rejected query."""
    for text in texts:
        found = _CODE.search(text)
        if found:
            return text[found.start() :].strip()[:500]
    return "TOOL_ERROR"


class McpUpstream:
    def __init__(
        self,
        urls: dict[str, str],
        *,
        timeout_seconds: float = 30.0,
        http_factory: Callable[[str, str], httpx2.AsyncClient] | None = None,
        trace_context: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self._trace_context = trace_context
        self._urls = urls
        self._timeout = timeout_seconds
        self._http_factory = http_factory
        self._schemas: dict[str, dict[str, Any]] = {}

    def _open(self, server: str, token: str) -> Client:
        if server not in self._urls:
            raise UpstreamError("UPSTREAM_UNAVAILABLE")
        if self._http_factory is not None:
            http = self._http_factory(server, token)
        else:
            http = httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token}"}, timeout=self._timeout
            )
        return Client(streamable_http_client(self._urls[server], http_client=http))

    async def schema(self, tool: str, token: str) -> dict[str, Any]:
        if tool in self._schemas:
            return self._schemas[tool]
        server = tool.partition(".")[0]
        try:
            async with self._open(server, token) as client:
                listed = await client.list_tools()
        except UpstreamError:
            raise
        except BaseException as exc:  # mapped to a code below
            raise _classify(exc) from None
        for entry in listed.tools:
            self._schemas[f"{server}.{entry.name}"] = dict(entry.input_schema)
        if tool not in self._schemas:
            raise UpstreamError("UPSTREAM_ERROR", "TOOL_MISSING")
        return self._schemas[tool]

    async def call(self, tool: str, args: dict[str, Any], token: str) -> dict[str, Any]:
        server, _, name = tool.partition(".")
        try:
            async with self._open(server, token) as client:
                carrier = self._trace_context() if self._trace_context else None
                meta = cast("RequestParamsMeta | None", carrier)
                result = await client.call_tool(name, args, meta=meta)
        except UpstreamError:
            raise
        except BaseException as exc:
            raise _classify(exc) from None
        if result.is_error:
            texts = [getattr(c, "text", "") for c in result.content]
            raise UpstreamError("UPSTREAM_ERROR", _tool_error_code(texts))
        if not isinstance(result.structured_content, dict):
            raise UpstreamError("UPSTREAM_ERROR", "NO_STRUCTURED_OUTPUT")
        return dict(result.structured_content)
