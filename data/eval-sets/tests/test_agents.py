"""The LLM baseline agent's plumbing, with a fake model and a fake executor (no network)."""

from decimal import Decimal

import pytest
from copilot_evals.loader import load_gold_set
from copilot_evals.models import GoldItem, GoldSet
from copilot_models import FakeClient, ModelError, ModelErrorCode, ModelResponse, ModelRole, Usage
from eval_sets.agents import ExecResult, LlmNaiveAgent
from eval_sets.insights_v0 import DATA_FILE


class StubExecutor:
    def __init__(
        self, result: ExecResult, schema: str = "treasury.transactions(amount, status)"
    ) -> None:
        self.result = result
        self.schema = schema
        self.calls: list[tuple[str, str]] = []

    async def run(self, persona: str, sql: str) -> ExecResult:
        self.calls.append((persona, sql))
        return self.result

    async def schema_text(self, persona: str) -> str:
        return self.schema


def _model_says(sql: str, *, cost: str | None = "0.0004") -> ModelResponse:
    return ModelResponse(
        text='{"sql": "' + sql + '"}',
        tool_calls=[],
        usage=Usage(input_tokens=120, output_tokens=30, cost_usd=Decimal(cost) if cost else None),
        model="m",
        latency_ms=250,
        finish_reason="stop",
    )


@pytest.fixture(scope="module")
def gold() -> GoldSet:
    return load_gold_set(DATA_FILE)


@pytest.fixture
def item(gold: GoldSet) -> GoldItem:
    return next(i for i in gold.items if i.expected_rows and i.reference_sql)


async def test_it_asks_the_model_for_sql_and_runs_it_as_the_persona(item: GoldItem) -> None:
    model = FakeClient([_model_says("SELECT 1")])
    executor = StubExecutor(ExecResult(rows=[["1"]], latency_ms=40))
    agent = LlmNaiveAgent(model, executor)
    answer = await agent.answer(item)
    assert executor.calls == [(item.persona, "SELECT 1")]
    assert answer.sql == "SELECT 1" and answer.rows == [["1"]]
    assert (answer.input_tokens, answer.output_tokens) == (120, 30)
    assert answer.cost_usd == Decimal("0.0004")
    assert answer.latency_ms >= 250


async def test_the_prompt_carries_the_question_and_schema_but_never_the_gold_answer(
    item: GoldItem,
) -> None:
    model = FakeClient([_model_says("SELECT 1")])
    await LlmNaiveAgent(model, StubExecutor(ExecResult(rows=[]))).answer(item)
    request = model.requests[0]
    assert request.role is ModelRole.PLANNER
    assert request.prompt_version == "naive-sql@1"
    prompt = request.system + " ".join(m.content for m in request.messages)
    assert item.question in prompt
    assert "treasury.transactions" in prompt
    assert item.reference_sql and item.reference_sql not in prompt
    assert item.naive_sql is None or item.naive_sql not in prompt
    pinned = [str(c) for row in item.expected_rows or [] for c in row if c and len(str(c)) > 6]
    assert [cell for cell in pinned if cell in prompt] == []
    assert request.output_schema is not None and "sql" in request.output_schema["properties"]


async def test_a_model_failure_becomes_a_mapped_error_not_a_crash(item: GoldItem) -> None:
    model = FakeClient([ModelError(ModelErrorCode.RATE_LIMITED)])
    answer = await LlmNaiveAgent(model, StubExecutor(ExecResult(rows=[]))).answer(item)
    assert answer.error_code == "rate_limited" and answer.rows is None


async def test_malformed_model_output_is_an_invalid_response(item: GoldItem) -> None:
    bad = _model_says("x").model_copy(update={"text": "not json"})
    answer = await LlmNaiveAgent(FakeClient([bad]), StubExecutor(ExecResult(rows=[]))).answer(item)
    assert answer.error_code == "invalid_response"


async def test_an_executor_error_is_carried_through(item: GoldItem) -> None:
    executor = StubExecutor(ExecResult(rows=None, error_code="PERMISSION", latency_ms=5))
    answer = await LlmNaiveAgent(FakeClient([_model_says("SELECT 1")]), executor).answer(item)
    assert answer.error_code == "PERMISSION" and answer.rows is None and answer.sql == "SELECT 1"


async def test_an_empty_query_means_the_model_declined(item: GoldItem) -> None:
    answer = await LlmNaiveAgent(
        FakeClient([_model_says("")]), StubExecutor(ExecResult(rows=[]))
    ).answer(item)
    assert answer.refused and answer.rows is None


async def test_the_answer_text_states_the_rows_it_found(item: GoldItem) -> None:
    executor = StubExecutor(ExecResult(rows=[["348076.61"]]))
    answer = await LlmNaiveAgent(FakeClient([_model_says("SELECT 1")]), executor).answer(item)
    assert answer.text is not None and "348076.61" in answer.text


async def test_it_never_attempts_side_effects(item: GoldItem) -> None:
    answer = await LlmNaiveAgent(
        FakeClient([_model_says("SELECT 1")]), StubExecutor(ExecResult(rows=[["1"]]))
    ).answer(item)
    assert answer.actions == ()


# --- the product-agent adapter ---------------------------------------------------------------


def test_untrusted_wrappers_are_removed_so_cells_compare_to_their_raw_values() -> None:
    from eval_sets.agents import unwrap_untrusted

    wrapped = (
        '<untrusted source="analytics.run_validated_select:remittance_text">INV 8841</untrusted>'
    )
    assert unwrap_untrusted(wrapped) == "INV 8841"
    neutralised = '<untrusted source="x">a &lt;/untrusted> b</untrusted>'
    assert unwrap_untrusted(neutralised) == "a </untrusted> b"
    assert unwrap_untrusted(776) == 776 and unwrap_untrusted("plain") == "plain"


async def test_the_adapter_maps_each_orchestrator_outcome_for_the_scorer(item: GoldItem) -> None:
    from eval_sets.agents import OrchestratorAgent
    from orchestrator import Answer, AnswerStatus, AnswerTable

    class Fixed:
        def __init__(self, answer: Answer) -> None:
            self.answer = answer

        async def run(self, question: str, ctx: object) -> Answer:
            return self.answer

    async def ctx(persona: str) -> object:
        return object()

    answered = Answer(
        status=AnswerStatus.ANSWERED,
        text="There are 7.",
        table=AnswerTable(columns=["n"], rows=[[7]], row_count=1),
        sql="SELECT 1",
    )
    mapped = await OrchestratorAgent(Fixed(answered), ctx).answer(item)
    assert mapped.rows == [[7]] and mapped.sql == "SELECT 1" and not mapped.refused

    refused = Answer(status=AnswerStatus.REFUSED, refusal_reason="unsupported")
    assert (await OrchestratorAgent(Fixed(refused), ctx).answer(item)).refused
    clarify = Answer(status=AnswerStatus.CLARIFY, clarification="Which entity?")
    assert (await OrchestratorAgent(Fixed(clarify), ctx).answer(item)).clarified
    failed = Answer(status=AnswerStatus.FAILED, refusal_reason="budget")
    result = await OrchestratorAgent(Fixed(failed), ctx).answer(item)
    assert result.error_code == "budget" and result.rows is None


def test_the_last_accepted_report_supplies_the_regression_baseline(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    from eval_sets.run import previous_execution_accuracy

    assert previous_execution_accuracy("copilot", tmp_path) is None  # nothing accepted yet
    (tmp_path / "copilot.json").write_text(
        json.dumps({"metrics": {"execution_accuracy": {"successes": 21, "total": 25}}})
    )
    assert previous_execution_accuracy("copilot", tmp_path) == 0.84
    (tmp_path / "copilot.json").write_text("not json")
    assert previous_execution_accuracy("copilot", tmp_path) is None  # unreadable: say nothing
