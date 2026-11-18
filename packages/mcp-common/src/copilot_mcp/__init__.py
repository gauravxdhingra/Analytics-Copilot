"""Plumbing shared by every MCP server."""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

import anyio
from copilot_auth import AuthError, Principal, TokenValidator
from copilot_tracing import Tracing
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context, MCPServer


class CopilotTokenVerifier:
    """Adapts our validator to the MCP SDK's bearer-token hook. The audience is checked by the
    validator itself, so each server only accepts tokens issued for it."""

    def __init__(self, validator: TokenValidator, resource_url: str) -> None:
        self._validator = validator
        self._resource = resource_url

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            # Key fetches can block on the network, so keep them off the event loop.
            principal = await anyio.to_thread.run_sync(self._validator.validate, token)
        except AuthError:
            return None
        return access_token(token, principal, self._resource)


def access_token(token: str, principal: Principal, resource: str) -> AccessToken:
    return AccessToken(
        token=token,
        client_id=principal.authorized_party,
        scopes=list(principal.roles),
        resource=resource,
        subject=principal.subject,
        claims={"tenant_id": principal.tenant_id, "entities": list(principal.entities)},
    )


def new_server(
    name: str, validator: TokenValidator, *, issuer_url: str, resource_url: str
) -> MCPServer:
    return MCPServer(
        name,
        token_verifier=CopilotTokenVerifier(validator, resource_url),
        auth=AuthSettings(
            issuer_url=issuer_url,
            resource_server_url=resource_url,
            validate_token_resource=False,  # the validator checks the audience itself
        ),
    )


@contextmanager
def traced(tracing: Tracing | None, ctx: Context, server: str, tool: str) -> Iterator[None]:
    if tracing is None:
        yield
        return
    carrier = {k: str(v) for k, v in dict(ctx.request_context.meta or {}).items()}
    with ExitStack() as stack:
        stack.enter_context(tracing.attach(carrier))
        stack.enter_context(tracing.span("mcp.tool", tool=f"{server}.{tool}"))
        yield
