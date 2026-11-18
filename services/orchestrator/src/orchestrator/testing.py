"""Test doubles for the orchestrator."""

import json
from collections import deque
from decimal import Decimal
from pathlib import Path
from typing import Any

from copilot_auth import Principal
from copilot_catalogue import Catalogue
from copilot_models import FakeClient, ModelError, ModelResponse, Usage
from policy_gateway import RunContext, ToolOutcome

from orchestrator.caps import RunCaps
from orchestrator.orchestrator import InsightsOrchestrator


class FakeGateway:
    """Scripted tool outcomes. It records each call; running dry is a loud failure."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._queue: deque[ToolOutcome] = deque()

    def queue(self, *outcomes: ToolOutcome) -> None:
        self._queue.extend(outcomes)

    async def call(
        self, ctx: RunContext, agent_id: str, tool: str, args: dict[str, Any]
    ) -> ToolOutcome:
        self.calls.append((agent_id, tool, dict(args)))
        if not self._queue:
            raise AssertionError("the fake gateway has no scripted outcome left")
        return self._queue.popleft()


def make_ctx(roles: tuple[str, ...] = ("analyst",)) -> RunContext:
    return RunContext(
        run_id="run-1",
        trace_id="trace-1",
        principal=Principal(
            subject="u-1",
            tenant_id="halcyon",
            entities=("HAL-DE",),
            roles=roles,
            authorized_party="orchestrator",
        ),
        upstream_tokens={"analytics": "tok"},
    )


CATALOGUE = Catalogue.load(Path(__file__).resolve().parents[4] / "data" / "catalogue")
VERSIONS = {"policy": "1", "prompt": "test"}

COUNT_PLAN: dict[str, Any] = {
    "store": "treasury.transactions",
    "select": [{"column": "transactions.transaction_id", "agg": "count", "alias": "n"}],
    "filters": [],
    "limit": 10,
}


def reply(payload: dict[str, Any], *, cost: str | None = None) -> ModelResponse:
    return ModelResponse(
        text=json.dumps(payload),
        tool_calls=[],
        usage=Usage(
            input_tokens=100,
            output_tokens=20,
            cost_usd=None if cost is None else Decimal(cost),
        ),
        model="scripted",
        latency_ms=5,
        finish_reason="stop",
    )


def route(intent: str = "insights", clarification: str | None = None) -> ModelResponse:
    return reply({"intent": intent, "clarification": clarification, "reason": "test"})


def plan(body: dict[str, Any] | None = None) -> ModelResponse:
    return reply({"plan": body or COUNT_PLAN, "clarify": None, "decline": None})


def narrate(text: str) -> ModelResponse:
    return reply({"text": text})


def tool_output(
    rows: list[list[Any]], columns: list[str] | None = None, **extra: Any
) -> dict[str, Any]:
    names = columns or ["n"]
    return {
        "columns": [{"name": n, "type": "text"} for n in names],
        "rows": rows,
        "row_count": len(rows),
        "truncated": False,
        "as_of": "2026-06-30T23:00:00+00:00",
        "query_hash": "sha256:abc",
        "untrusted_columns": [],
        "warnings": ["not checked here: the semantic (model) review, the cost guard"],
        "catalogue_version": CATALOGUE.version,
        "evidence": [
            {
                "evidence_id": "ev-1",
                "provenance": "system_state",
                "source_tool": "analytics.run_validated_select",
                "source_ref": "sha256:abc",
                "snapshot_at": "2026-06-30T23:00:00+00:00",
            }
        ],
        **extra,
    }


def build(
    script: list[ModelResponse | ModelError], gateway: FakeGateway, caps: RunCaps | None = None
) -> tuple[InsightsOrchestrator, FakeClient]:
    model = FakeClient(script)
    return InsightsOrchestrator(model, gateway, CATALOGUE, caps or RunCaps(), VERSIONS), model


async def real_user_context(username: str) -> RunContext:
    """A run context for a demo user with a real Keycloak token addressed to the MCP server."""
    import os

    from copilot_auth import JwksKeyProvider, TokenValidator
    from copilot_auth.testing import keycloak_token_for

    issuer = f"{os.environ.get('KEYCLOAK_URL', 'http://localhost:8081')}/realms/halcyon"
    token = await keycloak_token_for(username, audience="mcp-analytics")
    drafts_token = await keycloak_token_for(username, audience="mcp-drafts")
    principal = TokenValidator(
        issuer=issuer,
        audience="mcp-analytics",
        keys=JwksKeyProvider(f"{issuer}/protocol/openid-connect/certs"),
    ).validate(token)
    return RunContext(
        run_id=f"run-{username}",
        trace_id="trace-e2e",
        principal=principal,
        upstream_tokens={"analytics": token, "drafts": drafts_token},
    )
