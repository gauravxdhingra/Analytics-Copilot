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


# --- a failing server fails fast -------------------------------------------------------------


def _counting(exc: BaseException, counter: list[int]) -> McpUpstream:
    upstream = McpUpstream(
        {"analytics": "http://mcp/mcp", "drafts": "http://mcp-drafts/mcp"}, failure_threshold=3
    )

    def open_failing(server: str, token: str) -> Any:
        counter.append(1)
        return _Raises(exc)

    upstream._open = open_failing  # type: ignore[method-assign]
    return upstream


async def test_a_server_that_keeps_failing_is_not_called_again_for_a_while() -> None:
    opened: list[int] = []
    upstream = _counting(RuntimeError("down"), opened)
    for _ in range(3):
        with pytest.raises(UpstreamError):
            await upstream.call("analytics.x", {}, "t")
    with pytest.raises(UpstreamError) as info:
        await upstream.call("analytics.x", {}, "t")
    assert info.value.code == "UPSTREAM_UNAVAILABLE"
    assert len(opened) == 3  # the fourth call never tried the server


async def test_one_failing_server_does_not_stop_the_other() -> None:
    opened: list[int] = []
    upstream = _counting(RuntimeError("down"), opened)
    for _ in range(3):
        with pytest.raises(UpstreamError):
            await upstream.call("analytics.x", {}, "t")
    before = len(opened)
    with pytest.raises(UpstreamError):
        await upstream.call("drafts.y", {}, "t")
    assert len(opened) == before + 1  # drafts is still tried: its breaker is its own


async def test_timeouts_count_toward_opening() -> None:
    opened: list[int] = []
    upstream = _counting(httpx2.ReadTimeout("slow"), opened)
    for _ in range(4):
        with pytest.raises(UpstreamError):
            await upstream.call("analytics.x", {}, "t")
    assert len(opened) == 3


async def test_a_refused_token_does_not_open_the_circuit() -> None:
    from mcp.shared.exceptions import MCPError

    opened: list[int] = []
    upstream = _counting(MCPError(code=-32001, message="no"), opened)
    for _ in range(6):
        with pytest.raises(UpstreamError) as info:
            await upstream.call("analytics.x", {}, "t")
        assert info.value.code == "UPSTREAM_REJECTED"
    assert len(opened) == 6  # a rejection is about the caller, not an outage
