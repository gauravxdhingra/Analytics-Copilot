"""Structure and independence checks for the Insights gold set (no database needed)."""

from pathlib import Path

import pytest
from copilot_evals.loader import load_gold_set
from copilot_evals.models import Behavior, Capability, GoldSet, TrapKind
from eval_sets.insights_v0 import DATA_FILE, build
from halcyon_world import generate_world


@pytest.fixture(scope="module")
def gold() -> GoldSet:
    return load_gold_set(DATA_FILE)


def test_the_committed_file_is_exactly_what_the_authoring_code_builds(gold: GoldSet) -> None:
    assert gold == build(generate_world(seed=7))


def test_it_is_pinned_to_the_world_version_and_seed(gold: GoldSet) -> None:
    assert (gold.world_version, gold.seed) == ("v1", 7)
    assert gold.name == "insights_v0"


def test_it_is_big_enough_and_covers_every_trap_kind(gold: GoldSet) -> None:
    assert len(gold.items) >= 30
    assert {i.trap for i in gold.items if i.trap} == set(TrapKind)


def test_every_persona_is_a_real_demo_user(gold: GoldSet) -> None:
    assert {i.persona for i in gold.items} <= {
        "ana.analyst",
        "ben.analyst",
        "maria.manager",
        "amit.approver",
    }


def test_each_value_trap_ships_the_naive_query_that_falls_into_it(gold: GoldSet) -> None:
    value_traps = {
        TrapKind.RESTATED_VERSION,
        TrapKind.CANCELLED_AFTER_SNAPSHOT,
        TrapKind.HALF_OPEN_WINDOW,
        TrapKind.FAN_OUT,
        TrapKind.CURRENCY_MIX,
    }
    for trap in value_traps:
        items = [i for i in gold.items if i.trap is trap]
        assert items, trap
        assert all(i.naive_sql for i in items), trap


def test_refusals_and_clarifications_are_a_meaningful_share(gold: GoldSet) -> None:
    refusing = [i for i in gold.items if i.behavior is not Behavior.ANSWER]
    assert len(refusing) >= 6


def test_injection_items_forbid_obeying_instructions_found_in_data(gold: GoldSet) -> None:
    injection = [i for i in gold.items if i.trap is TrapKind.INJECTION]
    assert len(injection) >= 3 and all(i.must_not_obey for i in injection)
    assert {i.capability for i in injection} == {Capability.INSIGHTS, Capability.DOCUMENTS}


def test_labelled_faults_appear_in_the_expected_answers(gold: GoldSet) -> None:
    world = generate_world(seed=7)
    by_id = {i.id: i for i in gold.items}
    flagged = {label.ref_id for label in world.labels if label.kind == "structuring"}
    limit_item = next(i for i in gold.items if "90%" in i.question)
    answered = {row[0] for row in limit_item.expected_rows or []}
    assert flagged <= answered
    changed = next(i for i in gold.items if "settlement instructions" in i.question.lower())
    ids = {row[0] for row in changed.expected_rows or []}
    assert {
        label.ref_id
        for label in world.labels
        if label.kind in {"ssi_change_then_payment", "legitimate_ssi_migration"}
        and label.ref_table == "counterparty"
    } <= ids
    assert by_id  # items are addressable by id


def test_the_data_file_lives_inside_the_package() -> None:
    assert Path(DATA_FILE).name == "insights_v0.yaml"
