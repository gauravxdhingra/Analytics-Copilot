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
    # One more than the cap, so the Query API can tell "exactly the cap" from "cut short".
    assert render(_plan(limit=5000), CATALOGUE).sql.endswith("LIMIT 5001")
    assert render(_plan(limit=10), CATALOGUE).sql.endswith("LIMIT 11")


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


def test_aggregating_through_a_chain_that_ends_in_a_one_to_many_join_is_refused() -> None:
    plan = _plan(
        select=[{"column": "transactions.amount", "agg": "sum"}],
        joins=["transactions.counterparty", "counterparties.ssis"],
    )
    with pytest.raises(PlanError) as info:
        render(plan, CATALOGUE)
    assert info.value.code == "FAN_OUT"


def test_a_many_to_one_chain_may_be_aggregated() -> None:
    plan = _plan(
        select=[{"column": "transactions.amount", "agg": "sum"}],
        joins=["transactions.counterparty"],
    )
    assert "JOIN" in render(plan, CATALOGUE).sql


@pytest.mark.parametrize(
    "item",
    [
        {
            "column": "transactions.remittance_text",
            "alias": "memo",
        },  # a renamed column escapes the label
        {"column": "transactions.remittance_text", "agg": "max"},  # an aggregate renames it too
        {"column": "transactions.remittance_text", "agg": "min"},
    ],
)
def test_untrusted_text_cannot_be_renamed_or_aggregated(item: dict[str, str]) -> None:
    # The label that makes the gateway wrap and taint a cell is matched by column name.
    with pytest.raises(PlanError) as info:
        render(_plan(select=[{"column": "transactions.transaction_id"}, item]), CATALOGUE)
    assert info.value.code == "UNTRUSTED_COLUMN"


def test_untrusted_text_may_be_selected_plainly_or_counted() -> None:
    plain = _plan(select=[{"column": "transactions.remittance_text"}])
    assert "remittance_text" in render(plain, CATALOGUE).sql
    counted = _plan(
        select=[{"column": "transactions.remittance_text", "agg": "count", "alias": "n"}]
    )
    assert "COUNT(" in render(counted, CATALOGUE).sql


def _ranked(**overrides: Any) -> QueryPlan:
    return _plan(
        select=[{"column": "transactions.transaction_id"}, {"column": "transactions.amount"}],
        order_by=[{"column": "transactions.amount"}, {"column": "transactions.transaction_id"}],
        limit=5,
        **overrides,
    )


def test_an_offset_pages_through_an_ordered_result() -> None:
    query = render(_ranked(offset=5), CATALOGUE)
    assert query.sql.endswith("LIMIT 6 OFFSET 5")  # the extra row still tells us there is more
    VERIFIER.verify(*_verify_args(query))


def test_no_offset_adds_nothing_to_the_sql() -> None:
    assert "OFFSET" not in render(_ranked(), CATALOGUE).sql


def test_paging_without_an_order_is_refused_because_the_pages_would_not_be_stable() -> None:
    with pytest.raises(PlanError) as info:
        render(_plan(offset=5, order_by=[]), CATALOGUE)
    assert info.value.code == "OFFSET_NEEDS_ORDER"


@pytest.mark.parametrize("bad", [-1, 100_001])
def test_an_offset_is_bounded(bad: int) -> None:
    with pytest.raises(ValueError, match="offset"):
        _plan(offset=bad)


def _dated(field: str | None, on: str) -> QueryPlan:
    return _plan(
        select=[{"column": "transactions.transaction_id"}],
        filters=[
            {"column": f"transactions.{on}", "op": ">=", "values": ["2026-06-01"]},
            {"column": f"transactions.{on}", "op": "<", "values": ["2026-07-01"]},
        ],
        date_field=field,
    )


def test_the_planned_date_field_reaches_the_verifier_options() -> None:
    query = render(_dated("value_date", "value_date"), CATALOGUE)
    assert query.options.date_field == "value_date"
    assert render(_dated(None, "booking_date"), CATALOGUE).options.date_field is None


def test_filtering_a_different_date_than_the_plan_says_is_caught_by_the_verifier() -> None:
    from copilot_sql_verifier import VerificationFailed

    wrong = render(_dated("value_date", "booking_date"), CATALOGUE)
    with pytest.raises(VerificationFailed) as info:
        VERIFIER.verify(*_verify_args(wrong))
    assert {v.code for v in info.value.violations} == {"DATE_FIELD"}
    VERIFIER.verify(*_verify_args(render(_dated("value_date", "value_date"), CATALOGUE)))


def test_an_unknown_date_field_name_is_refused_at_the_schema() -> None:
    with pytest.raises(ValueError, match="date_field"):
        _dated("Robert'); DROP TABLE", "booking_date")


def test_a_store_alias_resolves_to_the_full_store_name() -> None:
    by_alias = render(_plan(store="transactions"), CATALOGUE)
    assert by_alias.sql == render(_plan(), CATALOGUE).sql


def test_an_unknown_store_is_still_refused() -> None:
    with pytest.raises(PlanError) as info:
        render(_plan(store="payroll"), CATALOGUE)
    assert info.value.code == "UNKNOWN_STORE"


def test_negate_makes_an_outflow_total_a_positive_figure() -> None:
    plan = _plan(
        select=[{"column": "transactions.amount", "agg": "sum", "alias": "total", "negate": True}],
        filters=[
            {"column": "transactions.account_id", "op": "=", "values": ["A-DE-EUR-1"]},
            {"column": "transactions.amount", "op": "<", "values": [0]},
        ],
    )
    query = render(plan, CATALOGUE)
    assert query.sql.startswith("SELECT -SUM(transactions.amount) AS total FROM")
    VERIFIER.verify(query.sql, query.params, query.options)


def test_negate_on_a_plain_money_column_keeps_its_name() -> None:
    plan = _plan(
        select=[{"column": "transactions.amount", "negate": True}],
        filters=[{"column": "transactions.account_id", "op": "=", "values": ["A-DE-EUR-1"]}],
    )
    query = render(plan, CATALOGUE)
    assert query.sql.startswith("SELECT -transactions.amount AS amount FROM")
    VERIFIER.verify(query.sql, query.params, query.options)


def test_negate_only_applies_to_a_money_column() -> None:
    plan = _plan(select=[{"column": "transactions.version", "agg": "max", "negate": True}])
    with pytest.raises(PlanError) as info:
        render(plan, CATALOGUE)
    assert info.value.code == "BAD_NEGATE"


def test_the_currency_split_follows_the_requested_columns_and_precedes_the_totals() -> None:
    plan = _plan(
        select=[
            {"column": "transactions.entity_id"},
            {"column": "transactions.amount", "agg": "sum", "alias": "total"},
        ],
        filters=[],
    )
    sql = render(plan, CATALOGUE).sql
    assert sql.startswith(
        "SELECT transactions.entity_id, transactions.currency, SUM(transactions.amount) AS total"
    )


def test_round_wraps_a_money_aggregate() -> None:
    plan = _plan(
        select=[{"column": "transactions.amount", "agg": "avg", "alias": "average", "round": 2}]
    )
    query = render(plan, CATALOGUE)
    assert query.sql.startswith("SELECT ROUND(AVG(transactions.amount), 2) AS average FROM")
    VERIFIER.verify(query.sql, query.params, query.options)


def test_round_is_applied_after_negate() -> None:
    plan = _plan(
        select=[
            {
                "column": "transactions.amount",
                "agg": "sum",
                "alias": "total",
                "negate": True,
                "round": 2,
            }
        ]
    )
    query = render(plan, CATALOGUE)
    assert "ROUND(-SUM(transactions.amount), 2) AS total" in query.sql
    VERIFIER.verify(query.sql, query.params, query.options)


def test_round_only_applies_to_a_money_column() -> None:
    plan = _plan(select=[{"column": "transactions.version", "agg": "max", "round": 2}])
    with pytest.raises(PlanError) as info:
        render(plan, CATALOGUE)
    assert info.value.code == "BAD_ROUND"


@pytest.mark.parametrize("bad", [-1, 7])
def test_round_digits_are_bounded(bad: int) -> None:
    with pytest.raises(ValueError, match="round"):
        _plan(select=[{"column": "transactions.amount", "agg": "avg", "round": bad}])


def test_a_grouped_query_without_an_order_is_ordered_by_its_groups() -> None:
    plan = _plan(
        select=[
            {"column": "transactions.entity_id"},
            {"column": "transactions.amount", "agg": "sum", "alias": "total"},
        ],
        filters=[],
    )
    sql = render(plan, CATALOGUE).sql
    assert "GROUP BY transactions.entity_id, transactions.currency ORDER BY" in sql
    assert "ORDER BY transactions.entity_id, transactions.currency LIMIT" in sql


def test_an_explicit_order_comes_first_and_the_groups_break_ties() -> None:
    plan = _plan(
        select=[
            {"column": "transactions.entity_id"},
            {"column": "transactions.amount", "agg": "sum", "alias": "total"},
        ],
        filters=[],
        order_by=[{"column": "total", "descending": True}],
    )
    sql = render(plan, CATALOGUE).sql
    assert "ORDER BY total DESC, transactions.entity_id, transactions.currency LIMIT" in sql


def test_a_partial_order_is_completed_with_the_remaining_groups() -> None:
    plan = _plan(
        select=[
            {"column": "transactions.entity_id"},
            {"column": "transactions.amount", "agg": "sum", "alias": "total"},
        ],
        filters=[],
        order_by=[{"column": "transactions.entity_id", "descending": False}],
    )
    sql = render(plan, CATALOGUE).sql
    assert "ORDER BY transactions.entity_id, transactions.currency LIMIT" in sql


def test_a_plain_aggregate_has_no_order_to_add() -> None:
    assert "ORDER BY" not in render(_plan(), CATALOGUE).sql
