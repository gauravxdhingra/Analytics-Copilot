"""One positive and one negative test per rule (sql-and-query-api rule: no untested control)."""

from collections.abc import Sequence
from typing import Any

import pytest
from copilot_sql_verifier import VerificationFailed, Verifier, VerifyOptions


def codes(
    verifier: Verifier, sql: str, params: Sequence[Any] | None = None, **opts: Any
) -> set[str]:
    """The set of violation codes for `sql`, empty if it verifies."""
    try:
        verifier.verify(sql, params or [], VerifyOptions(**opts))
    except VerificationFailed as exc:
        return {v.code for v in exc.violations}
    return set()


T = "treasury.transactions"
BOOKED = "is_current AND status = $1"


# --- accepted shapes ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        (f"SELECT count(*) FROM {T} WHERE {BOOKED}", ["booked"]),
        (
            f"SELECT -sum(amount) FROM {T} WHERE account_id = $1 AND {BOOKED.replace('$1', '$2')} "
            "AND amount < 0 AND booking_date >= $3 AND booking_date < $4",
            ["A-DE-EUR-1", "booked", "2026-01-01", "2026-02-01"],
        ),
        (
            "SELECT currency, sum(closing) FROM treasury.daily_balances "
            "WHERE balance_date = $1 GROUP BY currency",
            ["2026-06-30"],
        ),
        (
            f"SELECT c.name, sum(t.amount) FROM {T} t JOIN treasury.counterparties c "
            "USING (counterparty_id) WHERE t.is_current AND t.status = $1 AND t.currency = $2 "
            "GROUP BY c.name",
            ["booked", "EUR"],
        ),
        (
            f"SELECT entity_id, currency, -sum(amount) FROM {T} WHERE {BOOKED} AND amount < 0 "
            "GROUP BY entity_id, currency",
            ["booked"],
        ),
        (
            "SELECT p.payment_id FROM treasury.payments p JOIN treasury.transactions t "
            "ON t.transaction_id = p.transaction_id AND t.version = p.transaction_version "
            "WHERE t.is_current AND t.status = $1",
            ["booked"],
        ),
        ("SELECT entity_id, approval_limit FROM treasury.entities ORDER BY entity_id", []),
        (
            f"SELECT booking_date FROM {T} WHERE {BOOKED} ORDER BY booking_date LIMIT 5000",
            ["booked"],
        ),
    ],
)
def test_well_formed_queries_verify(verifier: Verifier, sql: str, params: list[object]) -> None:
    assert codes(verifier, sql, params) == set()


# --- one negative per rule -----------------------------------------------------------------------


def test_syntax_and_non_selects_are_rejected(verifier: Verifier) -> None:
    assert codes(verifier, "SELECT * FROM ((") == {"SYNTAX"}
    assert codes(verifier, "DELETE FROM treasury.accounts") == {"NOT_SELECT"}
    assert codes(verifier, "SELECT 1; SELECT 2") == {"NOT_SELECT"}


def test_unknown_stores_and_columns_are_rejected(verifier: Verifier) -> None:
    assert "UNKNOWN_STORE" in codes(verifier, "SELECT 1 FROM treasury.invented")
    assert "UNKNOWN_STORE" in codes(verifier, "SELECT 1 FROM pg_catalog.pg_roles")
    assert "UNKNOWN_COLUMN" in codes(verifier, "SELECT invented FROM treasury.accounts")
    assert "UNKNOWN_COLUMN" in codes(verifier, "SELECT a.invented FROM treasury.accounts a")


def test_restricted_columns_are_rejected_unless_allowed(verifier: Verifier) -> None:
    assert codes(verifier, "SELECT iban FROM treasury.accounts") == {"SENSITIVE_COLUMN"}
    assert "SENSITIVE_COLUMN" in codes(verifier, "SELECT * FROM treasury.accounts")
    assert "SENSITIVE_COLUMN" in codes(
        verifier,
        f"SELECT 1 FROM {T} WHERE beneficiary_iban = $1 AND {BOOKED.replace('$1', '$2')}",
        ["x", "booked"],
    )
    assert codes(verifier, "SELECT iban FROM treasury.accounts", allow_restricted=True) == set()


def test_the_current_version_filter_is_mandatory_unless_history_is_requested(
    verifier: Verifier,
) -> None:
    sql = f"SELECT count(*) FROM {T} WHERE status = $1"
    assert codes(verifier, sql, ["booked"]) == {"CURRENT_VERSION"}
    assert codes(verifier, sql, ["booked"], history_requested=True) == set()
    # is_current = false does not satisfy it either
    assert "CURRENT_VERSION" in codes(
        verifier, f"SELECT count(*) FROM {T} WHERE is_current = false AND status = $1", ["booked"]
    )


def test_cancelled_rows_must_be_excluded_unless_asked_for(verifier: Verifier) -> None:
    sql = f"SELECT count(*) FROM {T} WHERE is_current"
    assert codes(verifier, sql) == {"STATUS_FILTER"}
    assert codes(verifier, sql, include_cancelled=True) == set()
    assert "STATUS_FILTER" in codes(
        verifier, f"SELECT count(*) FROM {T} WHERE is_current AND status <> $1", ["cancelled"]
    )


def test_the_mandatory_filters_apply_to_every_store_in_the_query(verifier: Verifier) -> None:
    sql = (
        "SELECT p.payment_id FROM treasury.payments p JOIN treasury.transactions t "
        "ON t.transaction_id = p.transaction_id AND t.version = p.transaction_version"
    )
    assert codes(verifier, sql) == {"CURRENT_VERSION", "STATUS_FILTER"}


def test_joining_to_a_one_to_many_store_while_summing_is_a_fan_out(verifier: Verifier) -> None:
    sql = (
        f"SELECT sum(t.amount) FROM {T} t JOIN treasury.ssis s USING (counterparty_id) "
        "WHERE t.is_current AND t.status = $1 AND t.account_id = $2"
    )
    assert codes(verifier, sql, ["booked", "A-DE-EUR-1"]) == {"FAN_OUT"}
    # Counting distinct grain keys is the sanctioned way to count through a one-to-many join.
    distinct = sql.replace("sum(t.amount)", "count(DISTINCT t.transaction_id)")
    assert codes(verifier, distinct, ["booked", "A-DE-EUR-1"]) == set()


def test_joins_must_follow_a_declared_relationship(verifier: Verifier) -> None:
    sql = (
        f"SELECT count(*) FROM {T} t JOIN treasury.banks b ON t.reference = b.bank_id "
        "WHERE t.is_current AND t.status = $1"
    )
    assert codes(verifier, sql, ["booked"]) == {"UNDECLARED_JOIN"}


def test_a_cross_join_is_rejected(verifier: Verifier) -> None:
    assert "CROSS_JOIN" in codes(verifier, "SELECT 1 FROM treasury.accounts a, treasury.banks b")
    assert "CROSS_JOIN" in codes(
        verifier, "SELECT 1 FROM treasury.accounts a CROSS JOIN treasury.banks b"
    )


def test_money_must_be_split_by_currency_or_pinned_to_one(verifier: Verifier) -> None:
    mixed = f"SELECT sum(amount) FROM {T} WHERE {BOOKED}"
    assert codes(verifier, mixed, ["booked"]) == {"CURRENCY_MIX"}
    assert "CURRENCY_MIX" in codes(
        verifier,
        "SELECT sum(closing) FROM treasury.daily_balances WHERE balance_date = $1",
        ["2026-06-30"],
    )
    # Grouping by entity alone still adds across currencies.
    by_entity = f"SELECT entity_id, sum(amount) FROM {T} WHERE {BOOKED} GROUP BY entity_id"
    assert codes(verifier, by_entity, ["booked"]) == {"CURRENCY_MIX"}
    pinned = f"SELECT sum(amount) FROM {T} WHERE {BOOKED.replace('$1', '$2')} AND currency = $1"
    assert codes(verifier, pinned, ["EUR", "booked"]) == set()
    grouped_by_account = (
        f"SELECT account_id, sum(amount) FROM {T} WHERE {BOOKED} GROUP BY account_id"
    )
    assert codes(verifier, grouped_by_account, ["booked"]) == set()  # one account, one currency


def test_a_currency_filter_with_several_values_does_not_pin_it(verifier: Verifier) -> None:
    sql = f"SELECT sum(amount) FROM {T} WHERE {BOOKED.replace('$1', '$3')} AND currency IN ($1, $2)"
    assert codes(verifier, sql, ["EUR", "GBP", "booked"]) == {"CURRENCY_MIX"}


def test_date_windows_must_be_half_open(verifier: Verifier) -> None:
    closed = f"SELECT count(*) FROM {T} WHERE {BOOKED.replace('$1', '$3')} AND booking_date >= $1 AND booking_date <= $2"
    assert codes(verifier, closed, ["2026-01-01", "2026-02-01", "booked"]) == {"DATE_INTERVAL"}
    between = f"SELECT count(*) FROM {T} WHERE {BOOKED.replace('$1', '$3')} AND booking_date BETWEEN $1 AND $2"
    assert codes(verifier, between, ["2026-01-01", "2026-02-01", "booked"]) == {"DATE_INTERVAL"}
    exclusive_start = f"SELECT count(*) FROM {T} WHERE {BOOKED.replace('$1', '$3')} AND booking_date > $1 AND booking_date < $2"
    assert codes(verifier, exclusive_start, ["2026-01-01", "2026-02-01", "booked"]) == {
        "DATE_INTERVAL"
    }


def test_a_single_bound_or_an_equality_is_not_a_window(verifier: Verifier) -> None:
    one_bound = "SELECT count(*) FROM treasury.ssis WHERE effective_from <= $1"
    assert codes(verifier, one_bound, ["2026-05-01"]) == set()
    assert (
        codes(verifier, "SELECT rate FROM treasury.fx_rates WHERE rate_date = $1", ["2026-05-15"])
        == set()
    )


def test_the_date_field_must_match_the_requested_semantics(verifier: Verifier) -> None:
    sql = f"SELECT count(*) FROM {T} WHERE {BOOKED.replace('$1', '$3')} AND value_date >= $1 AND value_date < $2"
    params = ["2026-01-01", "2026-02-01", "booked"]
    assert codes(verifier, sql, params) == set()
    assert (
        codes(
            verifier,
            sql,
            params,
        )
        == set()
    )
    with pytest.raises(VerificationFailed) as info:
        verifier.verify(sql, params, VerifyOptions(date_field="booking_date"))
    assert {v.code for v in info.value.violations} == {"DATE_FIELD"}


def test_literals_must_be_parameters(verifier: Verifier) -> None:
    assert codes(verifier, f"SELECT count(*) FROM {T} WHERE is_current AND status = 'booked'") == {
        "LITERAL"
    }
    assert "LITERAL" in codes(
        verifier, f"SELECT count(*) FROM {T} WHERE {BOOKED} AND amount < 0.9", ["booked"]
    )
    assert "LITERAL" in codes(
        verifier, f"SELECT count(*) FROM {T} WHERE {BOOKED} AND amount < 500000", ["booked"]
    )
    # Small structural integers are fine.
    assert (
        codes(
            verifier,
            f"SELECT round(avg(amount), 2) FROM {T} WHERE account_id = $2 AND {BOOKED}",
            ["booked", "A-DE-EUR-1"],
        )
        == set()
    )


def test_a_missing_parameter_is_rejected(verifier: Verifier) -> None:
    assert "PARAMS" in codes(
        verifier, f"SELECT count(*) FROM {T} WHERE is_current AND status = $1", []
    )


# --- feedback shape ----------------------------------------------------------------------------


def test_all_violations_are_returned_together_with_readable_reasons(verifier: Verifier) -> None:
    with pytest.raises(VerificationFailed) as info:
        verifier.verify(f"SELECT sum(amount) FROM {T}", [], VerifyOptions())
    violations = info.value.violations
    assert {v.code for v in violations} == {"CURRENT_VERSION", "STATUS_FILTER", "CURRENCY_MIX"}
    assert all(v.message for v in violations)
    assert info.value.code == "VERIFIER_REJECTED"


def test_a_pass_reports_what_was_not_checked(verifier: Verifier) -> None:
    warnings = verifier.verify(
        f"SELECT count(*) FROM {T} WHERE {BOOKED}", ["booked"], VerifyOptions()
    )
    text = " ".join(warnings)
    assert (
        "semantic" in text and "cost" in text
    )  # the LLM check and the cost guard are not part of this layer


def test_a_fan_out_two_joins_away_is_still_a_fan_out(verifier: Verifier) -> None:
    # transactions -> counterparties is many-to-one, so the repeat is introduced one hop later:
    # each transaction appears once per settlement instruction of its counterparty.
    sql = (
        f"SELECT sum(t.amount) FROM {T} t JOIN treasury.counterparties c USING (counterparty_id) "
        "JOIN treasury.ssis s ON s.counterparty_id = c.counterparty_id "
        "WHERE t.is_current AND t.status = $1 AND t.account_id = $2"
    )
    assert codes(verifier, sql, ["booked", "A-DE-EUR-1"]) == {"FAN_OUT"}


def test_a_chain_of_many_to_one_joins_is_not_a_fan_out(verifier: Verifier) -> None:
    sql = (
        f"SELECT sum(t.amount) FROM {T} t JOIN treasury.counterparties c USING (counterparty_id) "
        "WHERE t.is_current AND t.status = $1 AND t.account_id = $2"
    )
    assert codes(verifier, sql, ["booked", "A-DE-EUR-1"]) == set()


def test_counting_a_row_that_every_transaction_repeats_is_a_fan_out(verifier: Verifier) -> None:
    # Settlement instructions hang off the counterparty, so joining transactions to them repeats
    # each instruction once per transaction: the relationship is many-to-many, not one-to-many.
    sql = (
        f"SELECT count(s.ssi_id) FROM treasury.ssis s JOIN {T} t "
        "ON t.counterparty_id = s.counterparty_id WHERE t.is_current AND t.status = $1"
    )
    assert codes(verifier, sql, ["booked"]) == {"FAN_OUT"}


_PER_CURRENCY = (
    f"SELECT t.currency, sum(t.amount) AS total FROM {T} t "
    "WHERE t.is_current AND t.status = $1 GROUP BY t.currency"
)


def test_adding_up_per_currency_totals_in_an_outer_query_is_a_currency_mix(
    verifier: Verifier,
) -> None:
    # Each inner row is one currency, so the inner query is fine. Summing those rows is not.
    sql = f"SELECT sum(x.total) FROM ({_PER_CURRENCY}) x"
    assert codes(verifier, sql, ["booked"]) == {"CURRENCY_MIX"}


def test_the_same_mistake_through_a_cte_is_a_currency_mix(verifier: Verifier) -> None:
    sql = f"WITH x AS ({_PER_CURRENCY}) SELECT sum(x.total) FROM x"
    assert codes(verifier, sql, ["booked"]) == {"CURRENCY_MIX"}


def test_an_outer_query_that_keeps_the_currency_is_fine(verifier: Verifier) -> None:
    sql = f"SELECT x.currency, sum(x.total) FROM ({_PER_CURRENCY}) x GROUP BY x.currency"
    assert codes(verifier, sql, ["booked"]) == set()


def test_a_rollup_total_row_across_currencies_is_a_currency_mix(verifier: Verifier) -> None:
    sql = (
        f"SELECT t.currency, sum(t.amount) FROM {T} t WHERE t.is_current AND t.status = $1 "
        "GROUP BY ROLLUP (t.currency)"
    )
    assert codes(verifier, sql, ["booked"]) == {"CURRENCY_MIX"}


def test_cube_and_grouping_sets_are_refused_for_the_same_reason(verifier: Verifier) -> None:
    base = f"SELECT t.currency, sum(t.amount) FROM {T} t WHERE t.is_current AND t.status = $1 "
    assert codes(verifier, base + "GROUP BY CUBE (t.currency)", ["booked"]) == {"CURRENCY_MIX"}
    assert codes(verifier, base + "GROUP BY GROUPING SETS ((t.currency), ())", ["booked"]) == {
        "CURRENCY_MIX"
    }


def test_summing_distinct_amounts_merges_equal_payments_and_is_refused(verifier: Verifier) -> None:
    # Two different payments of the same amount are two payments. DISTINCT would count them once.
    base = f"FROM {T} t WHERE t.is_current AND t.status = $1 AND t.account_id = $2"
    for fn in ("sum", "avg"):
        sql = f"SELECT {fn}(DISTINCT t.amount) {base}"
        assert "DISTINCT_MONEY" in codes(verifier, sql, ["booked", "A-DE-EUR-1"]), fn


def test_counting_distinct_keys_and_plain_sums_are_still_allowed(verifier: Verifier) -> None:
    base = f"FROM {T} t WHERE t.is_current AND t.status = $1 AND t.account_id = $2"
    ok = f"SELECT count(DISTINCT t.transaction_id), sum(t.amount) {base}"
    assert codes(verifier, ok, ["booked", "A-DE-EUR-1"]) == set()


def test_a_cast_cannot_hide_a_closed_date_window(verifier: Verifier) -> None:
    # Wrapping the column in a cast made the date checks skip it, so a closed window passed.
    where = BOOKED.replace("$1", "$3")
    for wrapped in ("CAST(booking_date AS DATE)", "booking_date::date", "DATE(booking_date)"):
        sql = f"SELECT count(*) FROM {T} WHERE {where} AND booking_date >= $1 AND {wrapped} <= $2"
        assert codes(verifier, sql, ["2026-01-01", "2026-02-01", "booked"]) == {"DATE_INTERVAL"}, (
            wrapped
        )


def test_a_cast_cannot_hide_the_wrong_date_field(verifier: Verifier) -> None:
    sql = (
        f"SELECT count(*) FROM {T} WHERE {BOOKED.replace('$1', '$3')} "
        "AND CAST(booking_date AS DATE) >= $1 AND CAST(booking_date AS DATE) < $2"
    )
    params = ["2026-01-01", "2026-02-01", "booked"]
    with pytest.raises(VerificationFailed) as info:
        verifier.verify(sql, params, VerifyOptions(date_field="value_date"))
    assert {v.code for v in info.value.violations} == {"DATE_FIELD"}
