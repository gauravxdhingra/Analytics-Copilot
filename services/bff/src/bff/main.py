"""Production composition for the laptop profile: `uv run uvicorn bff.main:app`.

The copilot runs in-process with its tool servers (see orchestrator.stack); the BFF talks to the
IdP, the approval service and the trace backend over HTTP. With no model key configured, asking
returns a mapped error and everything else (login, drafts, approvals) still works.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from copilot_auth import JwksKeyProvider, TokenValidator
from copilot_models import ModelClient, ModelError, ModelErrorCode, ModelRequest, ModelResponse
from copilot_tracing import Tracing, configure_tracing
from fastapi import FastAPI
from orchestrator import InsightsOrchestrator
from orchestrator.stack import local_stack
from policy_gateway.postgres_ledger import PostgresLedger

from bff.app import BffDeps, create_app
from bff.broker import ExchangeBroker
from bff.config import BffConfig
from bff.copilot import CopilotApi, OrchestratorCopilot
from bff.oidc import OidcClient
from bff.sessions import InMemorySessionStore


class _NoModel:
    """Used when no model key is configured: every call fails with a mapped auth error."""

    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise ModelError(ModelErrorCode.AUTH)


class _Late:
    """The copilot is built at startup, after the stack is up; requests before then fail closed."""

    impl: CopilotApi | None = None

    def _ready(self) -> CopilotApi:
        if self.impl is None:
            raise RuntimeError("starting")
        return self.impl

    async def ask(self, *args: Any) -> Any:
        return await self._ready().ask(*args)

    async def list_drafts(self, *args: Any) -> Any:
        return await self._ready().list_drafts(*args)

    async def submit_draft(self, *args: Any) -> Any:
        return await self._ready().submit_draft(*args)


_log = logging.getLogger("bff")


def _scripted_model() -> ModelClient:
    """A canned model for demos and browser tests: it plans one fixed, still-verified query.

    The numbers and rows still come from the real stack, as the signed-in user; only the
    language model is replaced. Never selected unless COPILOT_MODEL=scripted is set.
    """
    from copilot_models import FakeClient
    from orchestrator.testing import COUNT_PLAN, narrate, plan, route

    _log.warning("COPILOT_MODEL=scripted: answers use a canned model, not a real one")
    return FakeClient(
        [route(), plan(COUNT_PLAN), narrate("There are some booked transactions.")] * 500
    )


def _model() -> ModelClient:
    if os.environ.get("COPILOT_MODEL") == "scripted":
        return _scripted_model()
    if os.environ.get("GEMINI_API_KEY"):
        from copilot_models.adapters.gemini import GeminiClient, GeminiSettings

        return GeminiClient(GeminiSettings.from_env())
    return _NoModel()


def build_app(env: dict[str, str] | None = None) -> FastAPI:
    env = dict(os.environ) if env is None else env
    config = BffConfig.from_env(env)
    jwks = JwksKeyProvider(f"{config.back_issuer}/protocol/openid-connect/certs")
    late = _Late()
    host = env.get("POSTGRES_HOST", "localhost:5433")
    tracing = Tracing(
        configure_tracing(
            "copilot-bff", otlp_endpoint=env.get("OTLP_ENDPOINT", "http://localhost:4318")
        )
    )

    deps = BffDeps(
        config=config,
        oidc=OidcClient(config, httpx.AsyncClient(timeout=5.0), jwks),
        sessions=InMemorySessionStore(
            idle_seconds=config.session_idle_seconds,
            absolute_seconds=config.session_absolute_seconds,
            login_ttl_seconds=config.login_ttl_seconds,
        ),
        access_validator=TokenValidator(issuer=config.issuer, audience="copilot-bff", keys=jwks),
        broker=ExchangeBroker(
            token_url=f"{config.back_issuer}/protocol/openid-connect/token",
            bff_secret=env["BFF_CLIENT_SECRET"],
            orchestrator_secret=env["ORCHESTRATOR_CLIENT_SECRET"],
        ),
        copilot=late,
        approvals=httpx.AsyncClient(
            base_url=env.get("APPROVAL_URL", "http://localhost:8002"), timeout=10.0
        ),
        jaeger=httpx.AsyncClient(
            base_url=env.get("JAEGER_URL", "http://localhost:16686"), timeout=5.0
        ),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        ledger = PostgresLedger(
            f"postgresql://copilot_gateway:{env['GATEWAY_DB_PASSWORD']}@{host}/analytics"
        )
        async with local_stack(ledger, tracing=tracing, env=env) as stack:
            orchestrator = InsightsOrchestrator(
                _model(),
                stack.gateway,
                stack.catalogue,
                versions={"policy": str(stack.policy.version)},
                tracing=tracing,
            )
            late.impl = OrchestratorCopilot(
                orchestrator,
                stack.gateway,
                deps.broker,
                TokenValidator(issuer=config.issuer, audience="mcp-analytics", keys=jwks),
            )
            yield

    return create_app(deps, lifespan=lifespan)


def __getattr__(name: str) -> FastAPI:
    # `uvicorn bff.main:app` builds the app lazily, so importing the module has no side effects.
    if name == "app":
        return build_app()
    raise AttributeError(name)
