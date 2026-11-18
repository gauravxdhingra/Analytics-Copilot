"""Per-hop token exchange (RFC 8693). The browser's session token is never passed through."""

from typing import Protocol

from copilot_auth import AuthError, TokenExchangeClient


class BrokerError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TokenBroker(Protocol):
    async def upstream_tokens(self, access_token: str) -> dict[str, str]:
        """Tokens for the tool servers, one per audience, derived from the user's session."""

    async def approval_token(self, access_token: str) -> str:
        """A token addressed to the approval service."""


class ExchangeBroker:
    def __init__(
        self,
        *,
        token_url: str,
        bff_secret: str,
        orchestrator_secret: str,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._bff = TokenExchangeClient(
            token_url=token_url,
            client_id="copilot-bff",
            client_secret=bff_secret,
            timeout_seconds=timeout_seconds,
        )
        self._orchestrator = TokenExchangeClient(
            token_url=token_url,
            client_id="orchestrator",
            client_secret=orchestrator_secret,
            timeout_seconds=timeout_seconds,
        )

    async def upstream_tokens(self, access_token: str) -> dict[str, str]:
        try:
            for_orchestrator = await self._bff.exchange(access_token, "orchestrator")
            return {
                "analytics": await self._orchestrator.exchange(for_orchestrator, "mcp-analytics"),
                "drafts": await self._orchestrator.exchange(for_orchestrator, "mcp-drafts"),
            }
        except AuthError as exc:
            raise BrokerError(exc.code.value) from None

    async def approval_token(self, access_token: str) -> str:
        try:
            return await self._bff.exchange(access_token, "approval-service")
        except AuthError as exc:
            raise BrokerError(exc.code.value) from None
