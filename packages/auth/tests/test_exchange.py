import httpx
import pytest
from copilot_auth import AuthError, AuthErrorCode, TokenExchangeClient

URL = "http://idp.test/token"


def _client(handler: httpx.MockTransport) -> TokenExchangeClient:
    return TokenExchangeClient(
        token_url=URL,
        client_id="orchestrator",
        client_secret="s3cret",  # test value
        timeout_seconds=2.0,
        transport=handler,
    )


async def test_exchange_sends_rfc8693_request_and_returns_token() -> None:
    seen: dict[str, str] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.update(dict(httpx.QueryParams(request.content.decode())))
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"access_token": "new-token"})

    token = await _client(httpx.MockTransport(handle)).exchange("subject-token", "mcp-analytics")
    assert token == "new-token"  # test value
    assert seen["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert seen["subject_token"] == "subject-token"
    assert seen["audience"] == "mcp-analytics"
    assert seen["scope"] == "aud-mcp-analytics"
    assert seen["auth"].startswith("Basic ")


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, {"error": "invalid_scope", "error_description": "secret internal detail"}),
        (403, {"error": "access_denied"}),
        (401, {"error": "unauthorized_client"}),
        (500, {"error": "boom"}),
    ],
)
async def test_idp_errors_map_to_a_code_without_leaking_detail(
    status: int, body: dict[str, str]
) -> None:
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(status, json=body)))
    with pytest.raises(AuthError) as info:
        await client.exchange("t", "orchestrator")
    assert info.value.code is AuthErrorCode.EXCHANGE_DENIED
    assert "secret internal detail" not in str(info.value)


async def test_unreachable_idp_fails_closed() -> None:
    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(AuthError) as info:
        await _client(httpx.MockTransport(down)).exchange("t", "orchestrator")
    assert info.value.code is AuthErrorCode.IDP_UNAVAILABLE


async def test_response_without_access_token_is_rejected() -> None:
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(200, json={"nope": 1})))
    with pytest.raises(AuthError) as info:
        await client.exchange("t", "orchestrator")
    assert info.value.code is AuthErrorCode.EXCHANGE_DENIED
