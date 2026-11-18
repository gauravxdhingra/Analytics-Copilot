"""Run against the local Keycloak: `make up` then `make test-integration`."""

import os

import httpx
import pytest
from copilot_auth import (
    AuthError,
    AuthErrorCode,
    JwksKeyProvider,
    TokenExchangeClient,
    TokenValidator,
)

pytestmark = pytest.mark.integration

BASE = os.environ.get("KEYCLOAK_URL", "http://localhost:8081")
ISSUER = f"{BASE}/realms/halcyon"
TOKEN_URL = f"{ISSUER}/protocol/openid-connect/token"
KEYS = JwksKeyProvider(f"{ISSUER}/protocol/openid-connect/certs")


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; load deploy/compose/.env")
    return value


def _validator(audience: str) -> TokenValidator:
    return TokenValidator(issuer=ISSUER, audience=audience, keys=KEYS)


def _exchanger(client_id: str, secret_env: str) -> TokenExchangeClient:
    return TokenExchangeClient(
        token_url=TOKEN_URL,
        client_id=client_id,
        client_secret=_env(secret_env),
        timeout_seconds=5.0,
    )


def _login(username: str) -> str:
    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": "test-harness",
            "username": username,
            "password": _env("DEMO_USER_PASSWORD"),
            "scope": "openid",
        },
        timeout=5.0,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


async def test_full_exchange_chain_keeps_identity_and_narrows_audience() -> None:
    user_token = _login("maria.manager")
    bff = _validator("copilot-bff").validate(user_token)
    assert set(bff.entities) == {"HAL-DE", "HAL-UK"}

    to_orchestrator = await _exchanger("copilot-bff", "BFF_CLIENT_SECRET").exchange(
        user_token, "orchestrator"
    )
    to_mcp = await _exchanger("orchestrator", "ORCHESTRATOR_CLIENT_SECRET").exchange(
        to_orchestrator, "mcp-analytics"
    )
    to_query_api = await _exchanger("mcp-analytics", "MCP_ANALYTICS_CLIENT_SECRET").exchange(
        to_mcp, "query-api"
    )

    for token, audience in [
        (to_orchestrator, "orchestrator"),
        (to_mcp, "mcp-analytics"),
        (to_query_api, "query-api"),
    ]:
        principal = _validator(audience).validate(token)
        assert principal.subject == bff.subject
        assert principal.entities == bff.entities
        assert principal.roles == ("treasury_manager",)

    # A token is only good for the hop it was issued to.
    with pytest.raises(AuthError) as info:
        _validator("orchestrator").validate(to_query_api)
    assert info.value.code is AuthErrorCode.WRONG_AUDIENCE


async def test_scope_is_per_user() -> None:
    ana = _validator("copilot-bff").validate(_login("ana.analyst"))
    ben = _validator("copilot-bff").validate(_login("ben.analyst"))
    assert ana.entities == ("HAL-DE",)
    assert ben.entities == ("HAL-UK",)
    assert ana.subject != ben.subject


@pytest.mark.parametrize(
    ("client", "secret_env", "audience"),
    [
        ("orchestrator", "ORCHESTRATOR_CLIENT_SECRET", "approval-service"),  # not an allowed hop
        ("orchestrator", "ORCHESTRATOR_CLIENT_SECRET", "query-api"),  # skipping a hop
    ],
)
async def test_disallowed_hops_are_refused(client: str, secret_env: str, audience: str) -> None:
    to_orchestrator = await _exchanger("copilot-bff", "BFF_CLIENT_SECRET").exchange(
        _login("ana.analyst"), "orchestrator"
    )
    with pytest.raises(AuthError) as info:
        await _exchanger(client, secret_env).exchange(to_orchestrator, audience)
    assert info.value.code is AuthErrorCode.EXCHANGE_DENIED


async def test_a_token_not_addressed_to_the_caller_cannot_be_exchanged() -> None:
    to_orchestrator = await _exchanger("copilot-bff", "BFF_CLIENT_SECRET").exchange(
        _login("ana.analyst"), "orchestrator"
    )
    with pytest.raises(AuthError) as info:
        await _exchanger("mcp-analytics", "MCP_ANALYTICS_CLIENT_SECRET").exchange(
            to_orchestrator, "query-api"
        )
    assert info.value.code is AuthErrorCode.EXCHANGE_DENIED


async def test_wrong_client_secret_is_refused() -> None:
    client = TokenExchangeClient(
        token_url=TOKEN_URL,
        client_id="orchestrator",
        client_secret="wrong",  # deliberately wrong
        timeout_seconds=5.0,
    )
    with pytest.raises(AuthError) as info:
        await client.exchange(_login("ana.analyst"), "mcp-analytics")
    assert info.value.code is AuthErrorCode.EXCHANGE_DENIED


async def test_unreachable_idp_fails_closed() -> None:
    client = TokenExchangeClient(
        token_url="http://127.0.0.1:9/token",  # closed port, not a secret
        client_id="orchestrator",
        client_secret="x",
        timeout_seconds=1.0,
    )
    with pytest.raises(AuthError) as info:
        await client.exchange("t", "mcp-analytics")
    assert info.value.code is AuthErrorCode.IDP_UNAVAILABLE
