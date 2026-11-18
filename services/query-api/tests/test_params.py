import pytest
from query_api.params import translate_params


def test_numbered_placeholders_become_named_psycopg_placeholders() -> None:
    sql, count = translate_params("SELECT 1 WHERE a >= $1 AND a < $2")
    assert sql == "SELECT 1 WHERE a >= %(p1)s AND a < %(p2)s"
    assert count == 2


def test_a_placeholder_can_be_reused() -> None:
    sql, count = translate_params("SELECT 1 WHERE a = $1 OR b = $1")
    assert sql.count("%(p1)s") == 2
    assert count == 1


def test_percent_signs_are_escaped_everywhere() -> None:
    sql, _ = translate_params("SELECT amount % 2 FROM t WHERE note LIKE '%fee%' AND x = $1")
    assert "amount %% 2" in sql
    assert "'%%fee%%'" in sql
    assert "%(p1)s" in sql


def test_dollar_signs_in_strings_and_comments_are_left_alone() -> None:
    sql, count = translate_params("SELECT '$1 is text' -- costs $2\n, $1")
    assert "'$1 is text'" in sql
    assert "$2" in sql  # inside the comment
    assert sql.endswith("%(p1)s")
    assert count == 1


def test_no_placeholders() -> None:
    assert translate_params("SELECT 1") == ("SELECT 1", 0)


def test_count_is_the_highest_index_and_gaps_are_rejected() -> None:
    with pytest.raises(ValueError, match="gap"):
        translate_params("SELECT $1, $3")
    with pytest.raises(ValueError, match="gap"):
        translate_params("SELECT $0")
