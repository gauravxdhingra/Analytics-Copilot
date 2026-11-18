"""OpenID Connect client for the BFF: authorization code flow with PKCE (RFC 7636)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from copilot_auth import KeyProvider

from bff.config import BffConfig


class OidcError(Exception):
    """A mapped code. IdP error text never reaches the browser."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    id_token: str | None
    expires_in: int


class OidcClient:
    def __init__(
        self,
        config: BffConfig,
        http: httpx.AsyncClient,
        keys: KeyProvider,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._clock = clock
        self._config = config
        self._http = http
        self._keys = keys
        self._base = f"{config.issuer}/protocol/openid-connect"  # what the browser is sent to
        self._back = f"{config.back_issuer}/protocol/openid-connect"  # what this process calls

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        acr_values: str | None = None,
        force_login: bool = False,
    ) -> str:
        params = {
            "client_id": self._config.client_id,
            "response_type": "code",
            "redirect_uri": self._config.redirect_uri,
            "scope": self._config.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if acr_values:
            params["acr_values"] = acr_values  # step-up: ask the IdP for a stronger login
        if force_login:
            params["prompt"] = "login"
            params["max_age"] = "0"  # a fresh authentication, not a cached SSO session
        return f"{self._base}/auth?{urlencode(params)}"

    async def _token_request(self, form: dict[str, str]) -> TokenSet:
        try:
            response = await self._http.post(
                f"{self._back}/token",
                data=form,
                auth=(self._config.client_id, self._config.client_secret),
            )
        except httpx.TimeoutException:
            raise OidcError("idp_timeout") from None
        except httpx.TransportError:
            raise OidcError("idp_unavailable") from None
        if response.status_code >= 500:
            raise OidcError("idp_unavailable")
        if response.status_code != httpx.codes.OK:
            raise OidcError("grant_rejected")
        body: dict[str, Any] = response.json()
        access = body.get("access_token")
        if not isinstance(access, str) or not access:
            raise OidcError("grant_rejected")
        return TokenSet(
            access_token=access,
            refresh_token=body.get("refresh_token"),
            id_token=body.get("id_token"),
            expires_in=int(body.get("expires_in", 300)),
        )

    async def exchange_code(self, code: str, verifier: str) -> TokenSet:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._config.redirect_uri,
                "code_verifier": verifier,
            }
        )

    async def refresh(self, refresh_token: str) -> TokenSet:
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    def verify_id_token(self, id_token: str, nonce: str) -> dict[str, Any]:
        """Check signature, issuer, audience and the login's nonce. Raises OidcError."""
        try:
            header = jwt.get_unverified_header(id_token)
            kid = header.get("kid")
            key = self._keys.get(kid) if isinstance(kid, str) else None
            if header.get("alg") != "RS256" or key is None:
                raise OidcError("bad_id_token")
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self._config.client_id,
                issuer=self._config.issuer,
                # Time claims are checked below with our own clock, which tests can control.
                options={
                    "require": ["exp", "iss", "aud", "sub"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except jwt.PyJWTError:
            raise OidcError("bad_id_token") from None
        expires = claims.get("exp")
        if not isinstance(expires, int | float) or expires <= self._clock().timestamp():
            raise OidcError("bad_id_token")
        if claims.get("nonce") != nonce:
            raise OidcError("bad_nonce")
        return claims

    def end_session_url(self, id_token_hint: str | None) -> str:
        params = {
            "client_id": self._config.client_id,
            "post_logout_redirect_uri": f"{self._config.public_url}/",
        }
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        return f"{self._base}/logout?{urlencode(params)}"
