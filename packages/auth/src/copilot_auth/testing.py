"""Helpers for tests in this and other packages: mint tokens offline, or get real ones.

Lives in the package, not in a test directory, so any service's tests can import it.
"""

import os
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from copilot_auth.exchange import TokenExchangeClient

ISSUER = "http://idp.test/realms/halcyon"
AUDIENCE = "orchestrator"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
KID = "test-key-1"


def mint_token(key: RSAPrivateKey, *, kid: str = KID, alg: str = "RS256", **overrides: Any) -> str:
    """Sign a token with sensible defaults. A None override removes that claim."""
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "azp": "copilot-bff",
        "tenant_id": "halcyon",
        "entities": ["HAL-DE"],
        "roles": ["analyst"],
        "iat": int(NOW.timestamp()) - 10,
        "exp": int(NOW.timestamp()) + 290,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


# --- real tokens from the local Keycloak (`make up`) -------------------------------------------

# Each hop is (exchanging client, its secret env var, audience obtained).
_HOPS = [
    ("copilot-bff", "BFF_CLIENT_SECRET", "orchestrator"),
    ("orchestrator", "ORCHESTRATOR_CLIENT_SECRET", "mcp-analytics"),
    ("mcp-analytics", "MCP_ANALYTICS_CLIENT_SECRET", "query-api"),
]
_CACHE: dict[tuple[str, str], str] = {}


async def keycloak_token_for(username: str, *, audience: str = "query-api") -> str:
    """A real token for a demo user addressed to `audience`, via the real exchange chain."""
    key = (username, audience)
    if key in _CACHE:
        return _CACHE[key]
    base = os.environ.get("KEYCLOAK_URL", "http://localhost:8081")
    token_url = f"{base}/realms/halcyon/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=5.0) as http:
        response = await http.post(
            token_url,
            data={
                "grant_type": "password",
                "client_id": "test-harness",
                "username": username,
                "password": os.environ["DEMO_USER_PASSWORD"],
                "scope": "openid",
            },
        )
        response.raise_for_status()
    token = str(response.json()["access_token"])
    if audience != "copilot-bff":
        for client_id, secret_env, hop_audience in _HOPS:
            exchanger = TokenExchangeClient(
                token_url=token_url,
                client_id=client_id,
                client_secret=os.environ[secret_env],
                timeout_seconds=5.0,
            )
            token = await exchanger.exchange(token, hop_audience)
            if hop_audience == audience:
                break
    _CACHE[key] = token
    return token
