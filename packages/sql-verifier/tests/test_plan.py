"""QueryPlan rendering: code writes the SQL, the model only chooses from the catalogue."""

from pathlib import Path
from typing import Any

import pytest
from copilot_catalogue import Catalogue
from copilot_sql_verifier import PlanError, QueryPlan, VerificationFailed, Verifier, render
from hypothesis import given, settings
from hypothesis import strategies as st

CATALOGUE = Catalogue.load(Path(__file__).resolve().parents[3] / "data" / "catalogue")
VERIFIER = Verifier(CATALOGUE)
T = "treasury.transactions"


def _plan(**overrides: Any) -> QueryPlan:
    base: dict[str, Any] = {
        "store": T,
        "select": [{"column": "transactions.amount", "agg": "sum", "alias": "total"}],
        "filters": [{"column": "transactions.account_id", "op": "=", "values": ["A-DE-EUR-1"]}],
        "limit": 100,
    }
    return QueryPlan.model_validate(base | overrides)


def test_a_simple_plan_renders_parameterised_sql_with_the_mandatory_filters() -> None:
    query = render(_plan(), CATALOGUE)
    assert query.sql.startswith(
        "SELECT SUM(transactions.amount) AS total FROM treasury.transactions"
    )
    assert "transactions.is_current" in query.sql
    assert "transactions.status = $" in query.sql
    assert "booked" in query.params and "A-DE-EUR-1" in query.params
    assert "'" not in query.sql  # no literal text anywhere in the SQL
    VERIFIER.verify(query.sql, query.params, query.options)


def test_values_are_only_ever_parameters() -> None:
    hostile = "x'; DROP TABLE treasury.transactions;--"
    plan = _plan(filters=[{"column": "transactions.account_id", "op": "=", "values": [hostile]}])
    query = render(plan, CATALOGUE)
    assert hostile in query.params
    assert "DROP" not in query.sql and "x'" not in query.sql


def test_money_is_split_by_currency_unless_pinned() -> None:
    by_entity = _plan(
        select=[
            {"column": "transactions.entity_id"},
            {"column": "transactions.amount", "agg": "sum", "alias": "total"},
        ],
        filters=[],
    )
    sql = render(by_entity, CATALOGUE).sql
    assert "transactions.currency" in sql and "GROUP BY" in sql
    pinned = render(_plan(), CATALOGUE).sql  # one account pins one currency
    assert "GROUP BY" not in pinned and "transactions.currency" not in pinned


def test_waivers_change_the_options_and_drop_the_matching_filter() -> None:
    history = render(_plan(include_history=True), CATALOGUE)
    assert "is_current" not in history.sql and history.options.history_requested
    cancelled = render(_plan(include_cancelled=True), CATALOGUE)
    assert "status" not in cancelled.sql and cancelled.options.include_cancelled


def test_joins_are_named_relationships_and_inject_filters_for_each_store() -> None:
    plan = _plan(
        select=[
            {"column": "counterparties.name"},
            {"column": "transactions.amount", "agg": "sum", "alias": "total"},
        ],
        joins=["transactions.counterparty"],
        filters=[{"column": "transactions.currency", "op": "=", "values": ["EUR"]}],
    )
    query = render(plan, CATALOGUE)
    assert "JOIN treasury.counterparties AS counterparties ON" in query.sql
    VERIFIER.verify(query.sql, query.params, query.options)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"store": "treasury.invented"}, "UNKNOWN_STORE"),
        ({"select": [{"column": "transactions.invented"}]}, "UNKNOWN_COLUMN"),
        ({"select": [{"column": "transactions.beneficiary_iban"}]}, "SENSITIVE_COLUMN"),
        ({"joins": ["transactions.nowhere"]}, "UNKNOWN_JOIN"),
        (
            {"select": [{"column": "counterparties.name"}], "joins": ["payments.entity"]},
            "UNKNOWN_JOIN",
        ),
        ({"filters": [{"column": "transactions.x", "op": "=", "values": ["a"]}]}, "UNKNOWN_COLUMN"),
        ({"order_by": [{"column": "nonexistent"}]}, "UNKNOWN_COLUMN"),
    ],
)
def test_plans_that_name_things_outside_the_catalogue_are_refused(
    overrides: dict[str, Any], code: str
) -> None:
    with pytest.raises(PlanError) as info:
        render(_plan(**overrides), CATALOGUE)
    assert info.value.code == code


def test_aggregating_through_a_one_to_many_join_is_refused_at_plan_time() -> None:
    plan = _plan(
        select=[{"column": "transactions.amount", "agg": "sum"}],
        joins=["transactions.ssis"],
    )
    with pytest.raises(PlanError) as info:
        render(plan, CATALOGUE)
    assert info.value.code == "FAN_OUT"
    counted = _plan(
        select=[{"column": "transactions.transaction_id", "agg": "count_distinct", "alias": "n"}],
        joins=["transactions.ssis"],
    )
    VERIFIER.verify(*_verify_args(render(counted, CATALOGUE)))


def _verify_args(query: Any) -> tuple[str, list[Any], Any]:
    return query.sql, query.params, query.options


def test_a_limit_is_bounded_and_structural() -> None:
    with pytest.raises(ValueError, match="limit"):
        _plan(limit=0)
    with pytest.raises(ValueError, match="limit"):
        _plan(limit=5001)
    assert render(_plan(limit=5000), CATALOGUE).sql.endswith("LIMIT 5000")


def test_the_same_plan_always_renders_the_same_sql() -> None:
    assert render(_plan(), CATALOGUE) == render(_plan(), CATALOGUE)


# --- the property that matters: rendered SQL never fails verification --------------------------

DIMENSIONS = [
    "transactions.entity_id",
    "transactions.currency",
    "transactions.account_id",
    "transactions.counterparty_id",
    "transactions.booking_date",
]
JOINS = [
    [],
    ["transactions.counterparty"],
    ["transactions.account"],
    ["transactions.entity"],
    ["transactions.ssis"],
]

selects = st.lists(
    st.sampled_from(
        [
            {"column": "transactions.amount", "agg": "sum", "alias": "total"},
            {"column": "transactions.amount", "agg": "avg", "alias": "mean"},
            {"column": "transactions.transaction_id", "agg": "count_distinct", "alias": "n"},
            {"column": "transactions.transaction_id", "agg": "count", "alias": "rows"},
        ]
    ),
    min_size=1,
    max_size=2,
    unique_by=lambda s: s["alias"],
)
filters = st.lists(
    st.sampled_from(
        [
            {"column": "transactions.account_id", "op": "=", "values": ["A-DE-EUR-1"]},
            {"column": "transactions.currency", "op": "=", "values": ["EUR"]},
            {"column": "transactions.entity_id", "op": "in", "values": ["HAL-DE", "HAL-UK"]},
            {"column": "transactions.booking_date", "op": ">=", "values": ["2026-01-01"]},
            {"column": "transactions.booking_date", "op": "<", "values": ["2026-03-01"]},
            {"column": "transactions.amount", "op": "<", "values": ["0"]},
        ]
    ),
    max_size=4,
    unique_by=lambda f: (f["column"], f["op"]),
)


@settings(max_examples=150, deadline=None)
@given(
    measures=selects,
    dimensions=st.lists(st.sampled_from(DIMENSIONS), max_size=3, unique=True),
    where=filters,
    joins=st.sampled_from(JOINS),
    history=st.booleans(),
    cancelled=st.booleans(),
)
def test_a_rendered_plan_either_is_refused_by_the_planner_or_passes_the_verifier(
    measures: list[dict[str, Any]],
    dimensions: list[str],
    where: list[dict[str, Any]],
    joins: list[str],
    history: bool,
    cancelled: bool,
) -> None:
    plan = QueryPlan.model_validate(
        {
            "store": T,
            "select": [{"column": d} for d in dimensions] + measures,
            "joins": joins,
            "filters": where,
            "include_history": history,
            "include_cancelled": cancelled,
            "limit": 200,
        }
    )
    try:
        query = render(plan, CATALOGUE)
    except PlanError:
        return  # refused at plan time, for example a fan-out: never emitted as SQL
    try:
        VERIFIER.verify(query.sql, query.params, query.options)
    except VerificationFailed as exc:  # pragma: no cover - this is the failure being hunted
        raise AssertionError(
            f"rendered SQL failed verification: {query.sql} {exc.violations}"
        ) from exc
