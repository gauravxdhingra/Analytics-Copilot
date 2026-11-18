from pathlib import Path

import pytest
from copilot_evals.loader import dump_gold_set, load_gold_set
from copilot_evals.models import Behavior, Capability, GoldItem, GoldSet, TrapKind
from pydantic import ValidationError


def _item(**overrides: object) -> GoldItem:
    base: dict[str, object] = {
        "id": "GS-001",
        "capability": Capability.INSIGHTS,
        "persona": "ana.analyst",
        "question": "How many transactions?",
        "behavior": Behavior.ANSWER,
        "reference_sql": "SELECT 1",
        "expected_rows": [["1"]],
    }
    return GoldItem.model_validate(base | overrides)


def test_a_valid_item_loads() -> None:
    assert _item().id == "GS-001"


def test_ids_must_follow_the_pattern() -> None:
    with pytest.raises(ValidationError):
        _item(id="item-1")


def test_an_insights_answer_needs_reference_sql_and_expected_rows() -> None:
    with pytest.raises(ValidationError):
        _item(reference_sql=None)
    with pytest.raises(ValidationError):
        _item(expected_rows=None)


def test_a_scope_refusal_must_pin_an_empty_result() -> None:
    ok = _item(
        behavior=Behavior.REFUSE,
        trap=TrapKind.SCOPE,
        expected_rows=[],
        refusal_reason="scope",
    )
    assert ok.expected_rows == []
    with pytest.raises(ValidationError):
        _item(
            behavior=Behavior.REFUSE,
            trap=TrapKind.SCOPE,
            expected_rows=[["1"]],
            refusal_reason="scope",
        )


def test_a_naive_query_only_makes_sense_for_a_trap() -> None:
    with pytest.raises(ValidationError):
        _item(naive_sql="SELECT 2")
    assert _item(trap=TrapKind.CANCELLED_AFTER_SNAPSHOT, naive_sql="SELECT 2").naive_sql


def test_refusals_and_clarifications_need_a_reason() -> None:
    with pytest.raises(ValidationError):
        _item(
            behavior=Behavior.REFUSE,
            trap=TrapKind.UNANSWERABLE,
            reference_sql=None,
            expected_rows=None,
        )


def test_gold_set_rejects_duplicate_ids() -> None:
    with pytest.raises(ValidationError):
        GoldSet(name="x", world_version="v1", seed=7, items=[_item(), _item()])


def test_yaml_round_trip(tmp_path: Path) -> None:
    gold = GoldSet(name="x", world_version="v1", seed=7, items=[_item(), _item(id="GS-002")])
    path = tmp_path / "gold.yaml"
    dump_gold_set(gold, path)
    assert load_gold_set(path) == gold
