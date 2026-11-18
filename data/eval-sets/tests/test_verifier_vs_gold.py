"""The V1-10 verifier against the V1-6 gold set: the traps must be caught before a query runs."""

from pathlib import Path

import pytest
from copilot_catalogue import Catalogue
from copilot_evals.loader import load_gold_set
from copilot_evals.models import Behavior, Capability, GoldItem, TrapKind
from copilot_sql_verifier import VerificationFailed, Verifier, VerifyOptions, parameterize_literals
from eval_sets.insights_v0 import DATA_FILE

CATALOGUE_DIR = Path(__file__).resolve().parents[3] / "data" / "catalogue"
GOLD = load_gold_set(DATA_FILE)
ANSWERABLE = [i for i in GOLD.items if i.reference_sql and i.behavior is Behavior.ANSWER]
SCOPE = [i for i in GOLD.items if i.trap is TrapKind.SCOPE]
NAIVE = [i for i in GOLD.items if i.naive_sql]

EXPECTED_REASON = {
    TrapKind.RESTATED_VERSION: "CURRENT_VERSION",
    TrapKind.CANCELLED_AFTER_SNAPSHOT: "STATUS_FILTER",
    TrapKind.HALF_OPEN_WINDOW: "DATE_INTERVAL",
    TrapKind.FAN_OUT: "FAN_OUT",
    TrapKind.CURRENCY_MIX: "CURRENCY_MIX",
}


@pytest.fixture(scope="module")
def verifier() -> Verifier:
    return Verifier(Catalogue.load(CATALOGUE_DIR))


def _violations(verifier: Verifier, sql: str) -> set[str]:
    bound, params = parameterize_literals(sql)
    try:
        verifier.verify(bound, params, VerifyOptions())
    except VerificationFailed as exc:
        return {v.code for v in exc.violations}
    return set()


@pytest.mark.parametrize("item", ANSWERABLE + SCOPE, ids=lambda i: i.id)
def test_every_reference_query_passes_the_verifier(item: GoldItem, verifier: Verifier) -> None:
    assert item.reference_sql
    assert _violations(verifier, item.reference_sql) == set(), item.question


@pytest.mark.parametrize("item", NAIVE, ids=lambda i: i.id)
def test_every_naive_query_is_rejected_for_the_right_reason(
    item: GoldItem, verifier: Verifier
) -> None:
    assert item.naive_sql and item.trap
    assert EXPECTED_REASON[item.trap] in _violations(verifier, item.naive_sql), item.question


def test_the_verifier_covers_every_value_trap_kind() -> None:
    assert {i.trap for i in NAIVE} == set(EXPECTED_REASON)


def test_documents_items_have_no_sql_to_verify() -> None:
    assert all(i.reference_sql is None for i in GOLD.items if i.capability is Capability.DOCUMENTS)
