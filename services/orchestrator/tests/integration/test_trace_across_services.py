"""One trace across orchestrator, gateway, MCP server, verifier and Query API."""

import os
from typing import Any

import pytest
from copilot_models import FakeClient
from copilot_tracing import (
    InMemoryDecisionStore,
    PostgresDecisionStore,
    Tracing,
    ensure_decisions_schema,
)
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from orchestrator import AnswerStatus, InsightsOrchestrator
from orchestrator.stack import local_stack
from orchestrator.testing import COUNT_PLAN as COUNT
from orchestrator.testing import narrate, plan, real_user_context, route
from policy_gateway import Denied
from policy_gateway.postgres_ledger import PostgresLedger

pytestmark = pytest.mark.integration

QUESTION = "How many booked transactions are there? TRACE-SECRET-QUESTION"


def _tracing() -> tuple[Tracing, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return Tracing(provider), exporter


def _ancestors(span: ReadableSpan, spans: list[ReadableSpan]) -> list[str]:
    by_id = {s.context.span_id: s for s in spans}
    chain: list[str] = []
    parent = span.parent
    while parent is not None and parent.span_id in by_id:
        node = by_id[parent.span_id]
        chain.append(node.name)
        parent = node.parent
    return chain


async def test_a_single_trace_covers_every_hop_with_correct_nesting(
    pg_ledger: PostgresLedger,
) -> None:
    tracing, exporter = _tracing()
    decisions = InMemoryDecisionStore()
    async with local_stack(pg_ledger, tracing=tracing, env=os.environ) as stack:
        agent = InsightsOrchestrator(
            FakeClient([route(), plan(COUNT), narrate("There are 776 booked transactions.")]),
            stack.gateway,
            stack.catalogue,
            tracing=tracing,
            decisions=decisions,
        )
        answer = await agent.run(QUESTION, await real_user_context("ana.analyst"))
    assert answer.status is AnswerStatus.ANSWERED and answer.trace_id

    spans = list(exporter.get_finished_spans())
    assert {s.context.trace_id for s in spans} == {int(answer.trace_id, 16)}
    names = {s.name for s in spans}
    assert {
        "agent.run",
        "agent.node",
        "gen_ai.chat",
        "tool.call",
        "mcp.tool",
        "verifier.check",
        "query.execute",
    } <= names

    query = next(s for s in spans if s.name == "query.execute")
    chain = _ancestors(query, spans)
    assert chain[:3] == ["mcp.tool", "tool.call", "agent.node"] and chain[-1] == "agent.run"
    attrs = dict(query.attributes or {})
    assert attrs["copilot.outcome"] == "ok" and attrs["copilot.row_count"] == 1
    assert str(attrs["copilot.query_hash"]).startswith("sha256:")
    verifier = next(s for s in spans if s.name == "verifier.check")
    assert dict(verifier.attributes or {})["passed"] is True

    # Content never reaches spans, and the decision store has what the models saw.
    dumped = " ".join(str(s.attributes) for s in spans)
    assert "TRACE-SECRET-QUESTION" not in dumped and "SELECT" not in dumped
    chats = [s for s in spans if s.name == "gen_ai.chat"]
    assert all(decisions.for_span(format(c.context.span_id, "016x")) for c in chats)


async def test_ledger_rows_carry_the_same_trace_id_as_the_spans(pg_ledger: PostgresLedger) -> None:
    tracing, _ = _tracing()
    async with local_stack(pg_ledger, tracing=tracing, env=os.environ) as stack:
        agent = InsightsOrchestrator(
            FakeClient([route(), plan(COUNT), narrate("There are 776.")]),
            stack.gateway,
            stack.catalogue,
            tracing=tracing,
        )
        answer = await agent.run(QUESTION, await real_user_context("maria.manager"))
    assert pg_ledger.read_all()[-1].trace_id == answer.trace_id


async def test_a_denial_is_a_span_with_its_reason_and_reaches_no_server(
    pg_ledger: PostgresLedger,
) -> None:
    tracing, exporter = _tracing()
    async with local_stack(pg_ledger, tracing=tracing, env=os.environ) as stack:
        agent = InsightsOrchestrator(
            FakeClient([route(), plan(COUNT)]),
            stack.gateway,
            stack.catalogue,
            tracing=tracing,
        )
        answer = await agent.run(QUESTION, await real_user_context("amit.approver"))
    assert answer.refusal_reason == "not_permitted"
    spans = list(exporter.get_finished_spans())
    tool = next(s for s in spans if s.name == "tool.call")
    attrs = dict(tool.attributes or {})
    assert attrs["decision"] == "DENY" and attrs["reason"] == "ROLE_REQUIRED"
    assert not {"mcp.tool", "verifier.check", "query.execute"} & {s.name for s in spans}


async def test_a_verifier_rejection_is_visible_in_the_trace_with_its_codes(
    pg_ledger: PostgresLedger,
) -> None:
    tracing, exporter = _tracing()
    sql = "SELECT sum(amount) FROM treasury.transactions WHERE is_current AND status = $1"
    async with local_stack(pg_ledger, tracing=tracing, env=os.environ) as stack:
        ctx = await real_user_context("maria.manager")
        with tracing.run(
            run_id="r", capability="insights", user_id="u", tenant_id="t", versions={}
        ):
            outcome = await stack.gateway.call(
                ctx,
                "insights.query_planner",
                "analytics.run_validated_select",
                {"sql": sql, "params": ["booked"], "purpose": "insights:trace"},
            )
    assert isinstance(outcome, Denied)
    spans = list(exporter.get_finished_spans())
    check = next(s for s in spans if s.name == "verifier.check")
    attrs: dict[str, Any] = dict(check.attributes or {})
    assert attrs["passed"] is False and "CURRENCY_MIX" in attrs["codes"]
    assert "query.execute" not in {s.name for s in spans}  # the warehouse was never asked


def test_the_decision_store_is_durable_append_only_and_redacted(
    pg_ledger: PostgresLedger, owner_dsn: str
) -> None:
    import psycopg

    ensure_decisions_schema(owner_dsn, app_role="copilot_gateway", reset=True)
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    app_dsn = f"postgresql://copilot_gateway:{os.environ['GATEWAY_DB_PASSWORD']}@{host}/analytics"
    store = PostgresDecisionStore(app_dsn)
    store.save("t1", "s1", "prompt", "Refund DE89370400440532013000 for invoice 8841")
    store.save("t1", "s1", "completion", "Done")
    records = store.for_span("s1")
    assert [r.kind for r in records] == ["prompt", "completion"]
    assert (
        "DE89370400440532013000" not in records[0].content and "invoice 8841" in records[0].content
    )
    with psycopg.connect(app_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE ops.decisions SET content = 'x'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM ops.decisions")
    with (
        psycopg.connect(owner_dsn, autocommit=True) as conn,
        pytest.raises(psycopg.errors.RaiseException),
    ):
        conn.execute("DELETE FROM ops.decisions")
