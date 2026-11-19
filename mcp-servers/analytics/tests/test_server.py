"""Contract tests for the analytics MCP server, over real streamable HTTP with real auth."""

from collections.abc import Callable
from typing import Any

import pytest
from copilot_auth.testing import mint_token
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from mcp_analytics.testing import FakeExchanger, FakeQueryApi
from mcp_analytics.verifier import PassThroughVerifier

Connect = Callable[[str | None], Any]

SELECT_ARGS = {
    "sql": "SELECT transaction_id, remittance_text FROM treasury.transactions "
    "WHERE is_current AND status = $1",
    "purpose": "insights:test",
    "params": ["booked"],
    "expected_grain": "transaction",
}


async def _call(connect: Connect, token: str, tool: str, args: dict[str, Any]) -> Any:
    async with connect(token) as client:
        return await client.call_tool(tool, args)


async def test_the_tool_list_is_read_only_and_every_tool_has_an_output_schema(
    connect: Connect, token: str
) -> None:
    async with connect(token) as client:
        tools = (await client.list_tools()).tools
    names = {t.name for t in tools}
    assert names == {
        "list_stores",
        "describe_store",
        "resolve_business_term",
        "run_validated_select",
        "get_freshness",
    }
    assert not any(v in n for n in names for v in ("approve", "execute", "release", "write"))
    assert all(t.output_schema is not None for t in tools)


@pytest.mark.parametrize("bad", [None, "not-a-token"])
async def test_missing_or_garbage_tokens_are_refused(connect: Connect, bad: str | None) -> None:
    with pytest.raises(BaseException):  # noqa: B017  # an ExceptionGroup wrapping a 401
        async with connect(bad) as client:
            await client.list_tools()


async def test_a_token_for_another_audience_is_refused(
    connect: Connect, key: RSAPrivateKey
) -> None:
    wrong = mint_token(key, aud="orchestrator")
    with pytest.raises(BaseException):  # noqa: B017
        async with connect(wrong) as client:
            await client.list_tools()


async def test_an_expired_token_is_refused(connect: Connect, key: RSAPrivateKey) -> None:
    expired = mint_token(key, aud="mcp-analytics", exp=1)
    with pytest.raises(BaseException):  # noqa: B017
        async with connect(expired) as client:
            await client.list_tools()


async def test_select_exchanges_the_token_and_calls_the_query_api_as_the_user(
    connect: Connect, token: str, query_api: FakeQueryApi, exchanger: FakeExchanger
) -> None:
    result = await _call(connect, token, "run_validated_select", SELECT_ARGS)
    assert not result.is_error
    assert exchanger.requests == [(token, "query-api")]  # never the caller's own token
    call = query_api.calls[0]
    assert call.token == "exchanged-for-query-api"
    assert call.sql == SELECT_ARGS["sql"] and call.purpose == "insights:test"


async def test_select_returns_typed_rows_with_evidence_and_taint_marks(
    connect: Connect, token: str
) -> None:
    result = await _call(connect, token, "run_validated_select", SELECT_ARGS)
    out = result.structured_content
    assert out["row_count"] == 2 and out["truncated"] is False
    assert [c["name"] for c in out["columns"]] == ["transaction_id", "remittance_text"]
    assert out["untrusted_columns"] == ["remittance_text"]
    assert out["query_hash"].startswith("sha256:")
    evidence = out["evidence"][0]
    assert evidence["provenance"] == "system_state"
    assert evidence["source_tool"] == "analytics.run_validated_select"
    assert evidence["source_ref"] == out["query_hash"]
    assert evidence["snapshot_at"] is not None


async def test_select_reports_what_the_verifier_did_not_check(connect: Connect, token: str) -> None:
    out = (await _call(connect, token, "run_validated_select", SELECT_ARGS)).structured_content
    assert any("semantic" in w for w in out["warnings"])
    assert out["catalogue_version"].startswith("sha256:")


@pytest.mark.parametrize("verifier", [PassThroughVerifier()], indirect=True)
async def test_a_pass_through_verifier_says_plainly_that_it_checked_nothing(
    connect: Connect, token: str
) -> None:
    out = (await _call(connect, token, "run_validated_select", SELECT_ARGS)).structured_content
    assert any("unverified" in w for w in out["warnings"])


@pytest.mark.parametrize(
    ("sql", "params", "code"),
    [
        (
            "SELECT sum(amount) FROM treasury.transactions WHERE is_current AND status = $1",
            ["booked"],
            "CURRENCY_MIX",
        ),
        (
            "SELECT count(*) FROM treasury.transactions WHERE status = $1",
            ["booked"],
            "CURRENT_VERSION",
        ),
        ("SELECT count(*) FROM treasury.transactions WHERE is_current", [], "STATUS_FILTER"),
        ("SELECT iban FROM treasury.accounts", [], "SENSITIVE_COLUMN"),
        (
            "SELECT count(*) FROM treasury.transactions WHERE is_current AND status = 'booked'",
            [],
            "LITERAL",
        ),
    ],
)
async def test_the_real_verifier_refuses_with_reasons_and_the_query_never_runs(
    connect: Connect, token: str, query_api: FakeQueryApi, sql: str, params: list[Any], code: str
) -> None:
    result = await _call(
        connect, token, "run_validated_select", {"sql": sql, "params": params, "purpose": "t"}
    )
    assert result.is_error
    text = " ".join(getattr(c, "text", "") for c in result.content)
    assert "VERIFIER_REJECTED" in text and code in text
    assert query_api.calls == []


async def test_history_and_cancelled_rows_can_be_requested_explicitly(
    connect: Connect, token: str, query_api: FakeQueryApi
) -> None:
    sql = "SELECT count(*) FROM treasury.transactions"
    args = {"sql": sql, "purpose": "t", "include_history": True, "include_cancelled": True}
    result = await _call(connect, token, "run_validated_select", args)
    assert not result.is_error and len(query_api.calls) == 1


async def test_max_rows_reaches_the_query_api(
    connect: Connect, token: str, query_api: FakeQueryApi
) -> None:
    await _call(connect, token, "run_validated_select", {**SELECT_ARGS, "max_rows": 7})
    assert query_api.calls[0].max_rows == 7


@pytest.mark.parametrize("code", ["PERMISSION", "TIMEOUT", "SYNTAX", "NOT_SELECT"])
async def test_query_api_errors_become_a_mapped_code_and_nothing_else(
    connect: Connect, token: str, query_api: FakeQueryApi, code: str
) -> None:
    query_api.fail_with(code)
    result = await _call(connect, token, "run_validated_select", SELECT_ARGS)
    assert result.is_error
    text = " ".join(getattr(c, "text", "") for c in result.content)
    assert code in text and "password" not in text.lower() and "Traceback" not in text


class _Rejecting:
    def verify(self, sql: str, params: list[Any], flags: object) -> list[str]:
        from mcp_analytics.verifier import VerificationFailed

        raise VerificationFailed("VERIFIER_REJECTED")


@pytest.mark.parametrize("verifier", [_Rejecting()], indirect=True)
async def test_a_verifier_rejection_is_reported_and_the_query_is_never_run(
    connect: Connect, token: str, query_api: FakeQueryApi
) -> None:
    result = await _call(connect, token, "run_validated_select", SELECT_ARGS)
    assert result.is_error
    assert "VERIFIER_REJECTED" in " ".join(getattr(c, "text", "") for c in result.content)
    assert query_api.calls == []


async def test_list_and_describe_stores_come_from_the_schema_endpoint(
    connect: Connect, token: str
) -> None:
    stores = (await _call(connect, token, "list_stores", {})).structured_content
    by_id = {s["store_id"]: s for s in stores["stores"]}
    assert set(by_id) == {"treasury.transactions", "treasury.accounts"}  # what the fake exposes
    assert "cash transaction version" in by_id["treasury.transactions"]["definition"]
    described = (
        await _call(connect, token, "describe_store", {"store_id": "treasury.transactions"})
    ).structured_content
    assert {"transaction_id", "amount"} <= {c["name"] for c in described["columns"]}
    assert any("is_current" in f for f in described["required_filters"])
    assert any("cancelled" in issue for issue in described["known_issues"])
    assert any("ssis (many_to_many)" in r for r in described["relationships"])
    assert described["catalogue_version"].startswith("sha256:")


async def test_describing_an_unknown_store_is_a_mapped_error(connect: Connect, token: str) -> None:
    result = await _call(connect, token, "describe_store", {"store_id": "pg_catalog.pg_authid"})
    assert result.is_error
    assert "STORE_NOT_FOUND" in " ".join(getattr(c, "text", "") for c in result.content)


async def test_freshness_reports_the_snapshot_time(connect: Connect, token: str) -> None:
    out = (await _call(connect, token, "get_freshness", {})).structured_content
    assert out["as_of"].startswith("2026-")
    assert out["evidence"][0]["provenance"] == "system_state"


async def test_business_terms_resolve_from_the_glossary(connect: Connect, token: str) -> None:
    out = (
        await _call(connect, token, "resolve_business_term", {"term": "Cash Position"})
    ).structured_content
    assert "never summed across currencies" in out["definition"].lower()
    assert out["maps_to"].startswith("treasury.daily_balances")
    assert out["evidence"][0]["source_tool"] == "analytics.resolve_business_term"


async def test_an_unknown_business_term_is_a_mapped_error(connect: Connect, token: str) -> None:
    result = await _call(connect, token, "resolve_business_term", {"term": "gibberish"})
    assert result.is_error
    assert "TERM_NOT_FOUND" in " ".join(getattr(c, "text", "") for c in result.content)
