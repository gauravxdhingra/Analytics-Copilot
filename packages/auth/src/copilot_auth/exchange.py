import httpx

from copilot_auth.errors import AuthError, AuthErrorCode

_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
_ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"  # noqa: S105  # URN, not a secret


class TokenExchangeClient:
    """RFC 8693 exchange: trade a token addressed to us for one addressed to the next hop."""

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = token_url
        self._auth = (client_id, client_secret)
        self._timeout = timeout_seconds
        self._transport = transport

    async def exchange(self, subject_token: str, audience: str) -> str:
        # Keycloak only offers an audience through a matching optional scope, which is what
        # restricts each client to the hops the realm allows (ADR 0003).
        data = {
            "grant_type": _GRANT,
            "subject_token": subject_token,
            "subject_token_type": _ACCESS_TOKEN,
            "requested_token_type": _ACCESS_TOKEN,
            "audience": audience,
            "scope": f"aud-{audience}",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                response = await http.post(self._url, data=data, auth=self._auth)
        except httpx.TransportError as exc:
            raise AuthError(AuthErrorCode.IDP_UNAVAILABLE) from exc
        if response.status_code != httpx.codes.OK:
            raise AuthError(AuthErrorCode.EXCHANGE_DENIED)
        try:
            token = response.json().get("access_token")
        except ValueError as exc:
            raise AuthError(AuthErrorCode.EXCHANGE_DENIED) from exc
        if not isinstance(token, str) or not token:
            raise AuthError(AuthErrorCode.EXCHANGE_DENIED)
        return token
