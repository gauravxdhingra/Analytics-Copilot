"""The result cache is per user, sits behind authentication and verification, and never hides
an error. A hit saves the Query API call, nothing more."""

from collections.abc import Callable
from typing import Any

import pytest
from copilot_auth.testing import mint_token
from copilot_memory import InMemoryResultCache, ResultCache
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from mcp_analytics.testing import FakeQueryApi

Connect = Callable[[str | None], Any]

SELECT_ARGS: dict[str, Any] = {
    "sql": "SELECT transaction_id, remittance_text FROM treasury.transactions "
    "WHERE is_current AND status = $1",
    "purpose": "insights:test",
    "params": ["booked"],
}


@pytest.fixture
def cache() -> ResultCache | None:
    return InMemoryResultCache()


async def _select(connect: Connect, token: str, **overrides: Any) -> Any:
    async with connect(token) as client:
        return await client.call_tool("run_validated_select", {**SELECT_ARGS, **overrides})


async def test_the_second_identical_question_skips_the_query_api(
    connect: Connect, token: str, query_api: FakeQueryApi
) -> None:
    first = await _select(connect, token)
    second = await _select(connect, token)
    assert len(query_api.calls) == 1
    assert first.structured_content["cached"] is False
    assert second.structured_content["cached"] is True
    assert second.structured_content["rows"] == first.structured_content["rows"]


async def test_a_hit_keeps_the_evidence_and_the_untrusted_marks(
    connect: Connect, token: str
) -> None:
    await _select(connect, token)
    out = (await _select(connect, token)).structured_content
    assert out["untrusted_columns"] == ["remittance_text"]
    assert out["evidence"][0]["source_tool"] == "analytics.run_validated_select"
    assert out["as_of"] is not None  # a cached answer still says as of when


async def test_another_user_never_gets_someone_elses_cached_rows(
    connect: Connect, key: RSAPrivateKey, token: str, query_api: FakeQueryApi
) -> None:
    other = mint_token(key, aud="mcp-analytics", sub="user-2", entities=["HAL-UK"])
    await _select(connect, token)
    result = await _select(connect, other)
    assert result.structured_content["cached"] is False
    assert len(query_api.calls) == 2  # the Query API, which enforces scope, was asked again


async def test_another_tenant_with_the_same_user_id_gets_its_own_entry(
    connect: Connect, key: RSAPrivateKey, token: str, query_api: FakeQueryApi
) -> None:
    elsewhere = mint_token(key, aud="mcp-analytics", tenant_id="other-tenant")
    await _select(connect, token)
    await _select(connect, elsewhere)
    assert len(query_api.calls) == 2


@pytest.mark.parametrize(
    "change",
    [
        {
            "sql": SELECT_ARGS["sql"] + " AND currency = $2",
            "params": ["booked", "EUR"],
        },
        {"max_rows": 5},
        {"include_history": True},
        {"include_cancelled": True},
    ],
)
async def test_anything_that_changes_the_answer_is_a_different_entry(
    connect: Connect, token: str, query_api: FakeQueryApi, change: dict[str, Any]
) -> None:
    await _select(connect, token)
    await _select(connect, token, **change)
    assert len(query_api.calls) == 2


async def test_an_error_is_never_cached(
    connect: Connect, token: str, query_api: FakeQueryApi
) -> None:
    query_api.fail_with("UPSTREAM_UNAVAILABLE")
    failed = await _select(connect, token)
    assert failed.is_error
    query_api.recover()
    again = await _select(connect, token)
    assert not again.is_error and again.structured_content["cached"] is False
    assert len(query_api.calls) == 2


async def test_verification_still_runs_before_the_cache_is_consulted(
    connect: Connect, token: str, query_api: FakeQueryApi
) -> None:
    await _select(connect, token)
    unsafe = await _select(
        connect, token, sql="SELECT transaction_id FROM treasury.transactions", params=[]
    )
    assert unsafe.is_error  # the mandatory filters are missing: refused, never served
    assert len(query_api.calls) == 1


async def test_an_unauthenticated_caller_gets_nothing_even_when_the_cache_is_warm(
    connect: Connect, token: str
) -> None:
    await _select(connect, token)
    with pytest.raises(Exception):  # noqa: B017 - the transport refuses the connection
        await _select(connect, "not-a-token")
