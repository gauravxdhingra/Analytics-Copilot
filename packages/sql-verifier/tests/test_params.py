from copilot_sql_verifier import parameterize_literals


def test_strings_and_decimals_become_parameters_in_order() -> None:
    sql, params = parameterize_literals(
        "SELECT a FROM t WHERE s = 'booked' AND d >= '2026-01-01' AND x < 0.9 LIMIT 5"
    )
    assert "$1" in sql and "$2" in sql and "$3" in sql and "'booked'" not in sql
    assert params == ["booked", "2026-01-01", "0.9"]
    assert sql.endswith("LIMIT 5")  # a limit is structural


def test_small_integers_stay_and_large_ones_are_bound() -> None:
    sql, params = parameterize_literals("SELECT round(avg(x), 2) FROM t WHERE y > 500000")
    assert "$1" in sql and "500000" not in sql
    assert ", 2)" in sql  # the structural 2 stays
    assert params == [500000]


def test_there_is_nothing_to_bind_in_a_clean_query() -> None:
    assert parameterize_literals("SELECT 1 FROM t") == ("SELECT 1 FROM t", [])
