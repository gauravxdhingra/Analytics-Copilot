"""Cancellation must stay cancellation: a timeout or a client that went away is not 'unavailable'."""

import asyncio
from typing import Any

import httpx2
import pytest
from policy_gateway.gateway import UpstreamError
from policy_gateway.mcp_upstream import McpUpstream


class _Raises:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def __aenter__(self) -> Any:
        raise self._exc

    async def __aexit__(self, *args: object) -> None:
        return None


def _upstream(exc: BaseException) -> McpUpstream:
    upstream = McpUpstream({"analytics": "http://mcp/mcp"})

    def open_failing(server: str, token: str) -> Any:
        return _Raises(exc)

    upstream._open = open_failing  # type: ignore[method-assign]
    return upstream


async def test_cancellation_is_not_swallowed_by_a_tool_call() -> None:
    with pytest.raises(asyncio.CancelledError):
        await _upstream(asyncio.CancelledError()).call("analytics.x", {}, "t")


async def test_cancellation_wrapped_in_an_exception_group_is_not_swallowed() -> None:
    # Task groups inside the MCP client wrap what they cancel.
    wrapped = BaseExceptionGroup("tg", [asyncio.CancelledError()])
    with pytest.raises(BaseExceptionGroup):
        await _upstream(wrapped).call("analytics.x", {}, "t")


async def test_cancellation_is_not_swallowed_while_listing_tools() -> None:
    with pytest.raises(asyncio.CancelledError):
        await _upstream(asyncio.CancelledError()).schema("analytics.x", "t")


async def test_a_timeout_still_maps_to_its_code() -> None:
    with pytest.raises(UpstreamError) as info:
        await _upstream(httpx2.ReadTimeout("slow")).call("analytics.x", {}, "t")
    assert info.value.code == "UPSTREAM_TIMEOUT"


async def test_any_other_failure_still_fails_closed_as_unavailable() -> None:
    with pytest.raises(UpstreamError) as info:
        await _upstream(RuntimeError("boom")).call("analytics.x", {}, "t")
    assert info.value.code == "UPSTREAM_UNAVAILABLE"
