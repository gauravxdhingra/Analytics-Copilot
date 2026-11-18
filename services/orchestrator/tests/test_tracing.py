"""The orchestrator's spans: one trace per run, a stable tree, and no content in attributes."""

from typing import Any

import pytest
from copilot_models import FakeClient, ModelError, ModelErrorCode, ModelRole
from copilot_tracing import InMemoryDecisionStore, Tracing
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from orchestrator import AnswerStatus, InsightsOrchestrator
from orchestrator.testing import (
    CATALOGUE,
    FakeGateway,
    make_ctx,
    narrate,
    plan,
    route,
    tool_output,
)
from policy_gateway import Allowed

QUESTION = "How many booked transactions are there? SECRET-QUESTION-MARKER"


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracing(exporter: InMemorySpanExporter) -> Tracing:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return Tracing(provider)


def _by_name(exporter: InMemorySpanExporter, name: str) -> list[ReadableSpan]:
    return [s for s in exporter.get_finished_spans() if s.name == name]


def _agent(
    script: list[Any], gateway: FakeGateway, tracing: Tracing, decisions: Any = None
) -> tuple[InsightsOrchestrator, FakeClient]:
    model = FakeClient(script)
    agent = InsightsOrchestrator(
        model, gateway, CATALOGUE, versions={"policy": "1"}, tracing=tracing, decisions=decisions
    )
    return agent, model


async def test_a_run_is_one_trace_with_a_node_span_for_each_step(
    gateway: FakeGateway, tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    gateway.queue(Allowed(output=tool_output([[776]])))
    agent, _ = _agent([route(), plan(), narrate("There are 776.")], gateway, tracing)
    ctx = make_ctx()
    answer = await agent.run(QUESTION, ctx)

    spans = exporter.get_finished_spans()
    assert {s.context.trace_id for s in spans} == {int(answer.trace_id or "", 16)}
    (run,) = _by_name(exporter, "agent.run")
    assert run.parent is None
    nodes = [
        str(dict(s.attributes or {})["copilot.node"]) for s in _by_name(exporter, "agent.node")
    ]
    assert sorted(nodes) == sorted(
        ["route", "plan", "render", "execute", "analyse", "narrate", "finish"]
    )
    assert len(_by_name(exporter, "gen_ai.chat")) == 3
    assert ctx.trace_id == answer.trace_id  # ledger rows will carry the same id


async def test_model_spans_nest_under_their_node_and_carry_counts_not_text(
    gateway: FakeGateway, tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    gateway.queue(Allowed(output=tool_output([[776]])))
    agent, _ = _agent([route(), plan(), narrate("There are 776.")], gateway, tracing)
    await agent.run(QUESTION, make_ctx())
    nodes = {
        s.context.span_id: dict(s.attributes or {})["copilot.node"]
        for s in _by_name(exporter, "agent.node")
    }
    for chat in _by_name(exporter, "gen_ai.chat"):
        assert chat.parent is not None and chat.parent.span_id in nodes
        attrs = dict(chat.attributes or {})
        assert (
            attrs["gen_ai.usage.input_tokens"] == 100 and attrs["gen_ai.usage.output_tokens"] == 20
        )
        assert str(attrs["copilot.prompt.template_id"]).endswith("@1")
    roles = {
        dict(s.attributes or {})["copilot.model.role"] for s in _by_name(exporter, "gen_ai.chat")
    }
    assert roles == {ModelRole.ROUTER.value, ModelRole.PLANNER.value, ModelRole.NARRATIVE.value}


async def test_no_question_sql_or_result_text_appears_in_any_attribute(
    gateway: FakeGateway, tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    gateway.queue(Allowed(output=tool_output([["SECRET-CELL-VALUE-12345"]], columns=["note"])))
    agent, _ = _agent([route(), plan(), narrate("The note is shown.")], gateway, tracing)
    await agent.run(QUESTION, make_ctx())
    dumped = " ".join(str(s.attributes) for s in exporter.get_finished_spans())
    assert "SECRET-QUESTION-MARKER" not in dumped and "SECRET-CELL-VALUE" not in dumped
    assert "SELECT" not in dumped and "is_current" not in dumped


async def test_the_run_span_records_the_outcome_and_versions(
    gateway: FakeGateway, tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    agent, _ = _agent([route("unsupported")], gateway, tracing)
    await agent.run("What is our credit rating?", make_ctx())
    attrs = dict(_by_name(exporter, "agent.run")[0].attributes or {})
    assert attrs["copilot.outcome"] == AnswerStatus.REFUSED.value
    assert attrs["copilot.reason"] == "unsupported" and attrs["copilot.model_calls"] == 1
    assert attrs["copilot.version.catalogue"] == CATALOGUE.version
    assert attrs["copilot.version.policy"] == "1"


async def test_a_failed_model_call_marks_its_span_with_the_mapped_code(
    gateway: FakeGateway, tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    agent, _ = _agent([ModelError(ModelErrorCode.RATE_LIMITED)], gateway, tracing)
    await agent.run(QUESTION, make_ctx())
    chat = _by_name(exporter, "gen_ai.chat")[0]
    assert chat.status.status_code is StatusCode.ERROR
    assert dict(chat.attributes or {})["error.code"] == "rate_limited"


async def test_repairs_show_up_as_extra_plan_nodes_with_their_attempt_number(
    gateway: FakeGateway, tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    bad = {
        "store": "treasury.transactions",
        "select": [{"column": "transactions.nope"}],
        "limit": 5,
    }
    gateway.queue(Allowed(output=tool_output([[1]])))
    agent, _ = _agent([route(), plan(bad), plan(), narrate("There is 1.")], gateway, tracing)
    await agent.run(QUESTION, make_ctx())
    plans = [
        dict(s.attributes or {})
        for s in _by_name(exporter, "agent.node")
        if dict(s.attributes or {})["copilot.node"] == "plan"
    ]
    assert len(plans) == 2


async def test_prompts_and_completions_go_to_the_decision_store_keyed_by_span(
    gateway: FakeGateway, tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    store = InMemoryDecisionStore()
    gateway.queue(Allowed(output=tool_output([[776]])))
    agent, _ = _agent([route(), plan(), narrate("There are 776.")], gateway, tracing, store)
    answer = await agent.run(QUESTION, make_ctx())
    for chat in _by_name(exporter, "gen_ai.chat"):
        records = store.for_span(format(chat.context.span_id, "016x"))
        assert [r.kind for r in records] == ["prompt", "completion"]
        assert all(r.trace_id == answer.trace_id for r in records)
    planner = next(
        c
        for c in _by_name(exporter, "gen_ai.chat")
        if dict(c.attributes or {})["copilot.model.role"] == "planner"
    )
    assert (
        "SECRET-QUESTION-MARKER"
        in store.for_span(format(planner.context.span_id, "016x"))[0].content
    )


async def test_tracing_is_optional(gateway: FakeGateway) -> None:
    gateway.queue(Allowed(output=tool_output([[776]])))
    model = FakeClient([route(), plan(), narrate("There are 776.")])
    answer = await InsightsOrchestrator(model, gateway, CATALOGUE).run(QUESTION, make_ctx())
    assert answer.status is AnswerStatus.ANSWERED and answer.trace_id is None
