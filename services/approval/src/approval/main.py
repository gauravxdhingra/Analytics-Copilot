"""Composition for the laptop profile: `uv run uvicorn approval.main:app --port 8002`."""

import os
from collections.abc import Mapping

from copilot_auth import JwksKeyProvider, TokenValidator
from copilot_drafts import ApprovalRepo, PostgresEntitlements
from copilot_tracing import Tracing, configure_tracing
from fastapi import FastAPI
from policy_gateway.postgres_ledger import PostgresLedger

from approval.app import ApprovalConfig, create_app


def build_app(env: Mapping[str, str] | None = None) -> FastAPI:
    env = dict(os.environ) if env is None else env
    keycloak = env.get("KEYCLOAK_URL", "http://localhost:8081")
    internal = env.get("KEYCLOAK_INTERNAL_URL", keycloak)
    host = env.get("POSTGRES_HOST", "localhost:5433")
    # Tokens carry the browser-facing issuer; keys are fetched over the internal address.
    validator = TokenValidator(
        issuer=f"{keycloak}/realms/halcyon",
        audience="approval-service",
        keys=JwksKeyProvider(f"{internal}/realms/halcyon/protocol/openid-connect/certs"),
    )
    dsn = f"postgresql://copilot_approval:{env['APPROVAL_DB_PASSWORD']}@{host}/analytics"
    return create_app(
        ApprovalConfig(),
        validator=validator,
        repo=ApprovalRepo(dsn),
        entitlements=PostgresEntitlements(dsn),
        ledger=PostgresLedger(
            f"postgresql://copilot_gateway:{env['GATEWAY_DB_PASSWORD']}@{host}/analytics"
        ),
        tracing=Tracing(
            configure_tracing(
                "copilot-approval", otlp_endpoint=env.get("OTLP_ENDPOINT", "http://localhost:4318")
            )
        ),
    )


def __getattr__(name: str) -> FastAPI:
    # Built lazily so importing the module has no side effects (and no env is needed in tests).
    if name == "app":
        return build_app()
    raise AttributeError(name)
